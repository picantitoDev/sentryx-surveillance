"""Receptor WebRTC con ventanas TA-2 e inferencia desacoplada de la recepción."""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from uuid import uuid4

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from aiortc.contrib.media import MediaRelay
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from app.pipeline import PipelineConfig, WindowBuffer, prepare_frames
from dataclasses import asdict

logger = logging.getLogger(__name__)


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=1, max_length=100000)
    type: Literal["offer"]
    source_id: str = Field(default="camara_mari", pattern=r"^[a-zA-Z0-9_-]{1,64}$")


class ViewerOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sdp: str = Field(min_length=1, max_length=100000)
    type: Literal["offer"]
    session_id: str = Field(min_length=1, max_length=64)


@dataclass
class Session:
    pc: RTCPeerConnection
    source_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    frames: int = 0
    width: int | None = None
    height: int | None = None
    timestamp: float | None = None
    created: float = field(default_factory=time.monotonic)
    last_frame: float | None = None
    tasks: set = field(default_factory=set)
    buffer: WindowBuffer | None = None
    windows_processed: int = 0
    processing_errors: int = 0
    processing: bool = False
    latest_result: dict | None = None
    last_error: str | None = None
    relay: MediaRelay = field(default_factory=MediaRelay)
    source_track: object = None
    processing_track: object = None
    alert_policy: object = None
    policy_epoch: int = 0
    last_policy_window: int = 0
    pending_alert_interruption: bool = False

    def snapshot(self):
        return {"session_id": self.id, "source_id": self.source_id,
                "connection_state": self.pc.connectionState,
                "ice_state": self.pc.iceConnectionState,
                "frames_received": self.frames, "width": self.width,
                "height": self.height, "timestamp": self.timestamp,
                "last_frame_age_seconds": None if self.last_frame is None
                else round(time.monotonic() - self.last_frame, 3),
                "frames_sampled": self.buffer.sampled,
                "windows_created": self.buffer.created,
                "windows_processed": self.windows_processed,
                "windows_dropped": self.buffer.dropped,
                "windows_queued": self.buffer.queue.qsize(),
                "frames_in_partial_window": len(self.buffer.partial),
                "timestamp_resets": self.buffer.resets,
                "invalid_timestamps": self.buffer.invalid_timestamps,
                "processing": self.processing,
                "processing_errors": self.processing_errors,
                "last_processing_error": self.last_error,
                "pipeline_config": asdict(self.buffer.config),
                "latest_result": self.latest_result}


class WebRTCReceiver:
    def __init__(self, predict_window, config=None, *, buffer_factory=WindowBuffer,
                 predict_frames=None, policy_factory=None, detection_store=None, log_store=None, alert_store=None):
        self.alert_store = alert_store
        self.log_store = log_store
        self.detection_store = detection_store
        self.predict_window = predict_window
        self.config = config or PipelineConfig.from_env()
        self.buffer_factory = buffer_factory
        self.predict_frames = predict_frames
        self.policy_factory = policy_factory
        self.sessions: dict[str, Session] = {}
        self.viewers = {}
        self.lock = asyncio.Lock()
        self.router = APIRouter(prefix="/webrtc", tags=["WebRTC"])
        self.router.add_api_route("/offer", self.offer, methods=["POST"])
        self.router.add_api_route("/sessions", self.list_sessions, methods=["GET"])
        self.router.add_api_route("/sessions/{session_id}", self.status, methods=["GET"])
        self.router.add_api_route("/sessions/{session_id}", self.delete, methods=["DELETE"])
        self.router.add_api_route("/viewers/offer", self.viewer_offer, methods=["POST"])
        self.router.add_api_route("/viewers/{viewer_id}", self.viewer_status, methods=["GET"])
        self.router.add_api_route("/viewers/{viewer_id}", self.delete_viewer, methods=["DELETE"])

    async def log_event(self, event_type, message, *, session=None, **kwargs):
        if self.log_store is not None:
            if session is not None:
                kwargs.update(camera=session.source_id, session_id=session.id)
            await asyncio.to_thread(self.log_store.emit, event_type, message, **kwargs)

    async def interrupt_alert(self, session, reason):
        if self.alert_store is not None:
            count = await asyncio.to_thread(self.alert_store.interrupt, session.id, reason)
            if count:
                await self.log_event("alert_interrupted", "Evento interrumpido: " + reason,
                                     session=session, level="WARNING")

    async def viewer_status(self, viewer_id: str):
        viewer = self.viewers.get(viewer_id)
        if viewer is None:
            raise HTTPException(404, "Espectador no encontrado")
        return {"viewer_id": viewer_id, "session_id": viewer['session_id'],
                "connection_state": viewer['pc'].connectionState,
                "ice_state": viewer['pc'].iceConnectionState}

    async def delete_viewer(self, viewer_id: str):
        if viewer_id not in self.viewers:
            raise HTTPException(404, "Espectador no encontrado")
        await self.close_viewer(viewer_id)
        return {"viewer_id": viewer_id, "closed": True}

    async def close_viewer(self, viewer_id):
        viewer = self.viewers.pop(viewer_id, None)
        if viewer is None:
            return
        timer = viewer.get('timer')
        if timer and timer is not asyncio.current_task():
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        if viewer.get('track'):
            viewer['track'].stop()
        await viewer['pc'].close()

    async def viewer_timeout(self, viewer_id):
        await asyncio.sleep(30)
        viewer = self.viewers.get(viewer_id)
        if viewer and viewer['pc'].connectionState != 'connected':
            await self.close_viewer(viewer_id)

    async def viewer_offer(self, offer: ViewerOffer):
        async with self.lock:
            session = self.sessions.get(offer.session_id)
            if session is None:
                raise HTTPException(404, "La fuente ya no existe")
            if session.source_track is None or session.pc.connectionState != 'connected':
                raise HTTPException(409, "La fuente aún no está conectada")
            if len(self.viewers) >= 4:
                raise HTTPException(409, "Límite de cuatro espectadores alcanzado")
            viewer_id = str(uuid4())
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            viewer = {'pc': pc, 'session_id': session.id, 'track': None}
            self.viewers[viewer_id] = viewer

        @pc.on('connectionstatechange')
        async def on_state():
            if pc.connectionState in ('closed', 'failed'):
                await self.close_viewer(viewer_id)

        try:
            async with asyncio.timeout(15):
                await pc.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
                transceivers = pc.getTransceivers()
                # Una oferta recvonly genera una dirección local sendonly.
                if (len(transceivers) != 1 or transceivers[0].kind != 'video'
                        or transceivers[0].receiver.track is not None or pc.sctp is not None):
                    raise ValueError('El espectador debe ofrecer solo video recvonly')
                viewer['track'] = session.relay.subscribe(session.source_track, buffered=False)
                pc.addTrack(viewer['track'])
                await pc.setLocalDescription(await pc.createAnswer())
                if transceivers[0].currentDirection != 'sendonly':
                    raise ValueError('Se requiere recvonly en el emisor de la oferta')
            if session.id not in self.sessions or viewer_id not in self.viewers:
                raise ValueError('La fuente se cerró durante la negociación')
            viewer['timer'] = asyncio.create_task(self.viewer_timeout(viewer_id))
            return {'type': 'answer', 'sdp': pc.localDescription.sdp,
                    'viewer_id': viewer_id, 'session_id': session.id, 'source_id': session.source_id}
        except asyncio.CancelledError:
            await self.close_viewer(viewer_id)
            raise
        except Exception:
            await self.close_viewer(viewer_id)
            raise HTTPException(400, 'Oferta de espectador inválida o fuente cerrada; use video recvonly e ICE completo') from None

    async def list_sessions(self):
        return [session.snapshot() for session in self.sessions.values()]

    async def status(self, session_id: str):
        if session_id not in self.sessions:
            raise HTTPException(404, "Sesión no encontrada o ya cerrada")
        return self.sessions[session_id].snapshot()

    async def delete(self, session_id: str):
        if session_id not in self.sessions:
            raise HTTPException(404, "Sesión no encontrada o ya cerrada")
        await self.close(session_id)
        return {"session_id": session_id, "closed": True}

    async def close(self, session_id):
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        for viewer_id, viewer in list(self.viewers.items()):
            if viewer['session_id'] == session_id:
                await self.close_viewer(viewer_id)
        pending = [task for task in session.tasks if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        try:
            await self.interrupt_alert(session, "source_disconnected")
        except Exception:
            logger.exception("No se pudo interrumpir la alerta de la fuente")
            await self.log_event("alert_storage_error", "No se pudo actualizar la alerta al cerrar la fuente",
                                 session=session, level="ERROR")
        session.buffer.clear()
        session.latest_result = None
        session.alert_policy = None
        if session.processing_track:
            session.processing_track.stop()
        if session.source_track:
            session.source_track.stop()
        await session.pc.close()
        await self.log_event("source_disconnected", "Fuente cerrada", session=session)
        logger.info("Fuente cerrada: %s; frames=%s", session.id, session.frames)

    async def shutdown(self):
        for session_id in list(self.sessions):
            await self.close(session_id)
        for viewer_id in list(self.viewers):
            await self.close_viewer(viewer_id)

    def spawn(self, session, coroutine):
        task = asyncio.create_task(coroutine)
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)

    async def consume(self, session, track):
        try:
            while True:
                frame = await track.recv()
                self.accept_frame(session, frame)
                if session.frames == 1:
                    await self.log_event("source_connected", "Primer frame de la fuente recibido", session=session)
        except MediaStreamError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Falló la recepción WebRTC: %s", session.id)
            await self.log_event("source_error", "Falló la recepción de video", session=session, level="ERROR")
        finally:
            await self.close(session.id)

    def accept_frame(self, session, frame):
        session.frames += 1
        session.width, session.height = frame.width, frame.height
        session.timestamp = float(frame.time) if frame.time is not None else None
        session.last_frame = time.monotonic()
        if session.frames == 1:
            logger.info("Primer frame: %s (%sx%s)", session.id, frame.width, frame.height)
        previous_epoch = session.buffer.resets
        session.buffer.push(frame, session.timestamp)
        if session.alert_policy is not None and session.buffer.resets != previous_epoch:
            session.pending_alert_interruption = True
            session.alert_policy.reset()
            session.policy_epoch = session.buffer.resets
            session.last_policy_window = 0
            session.latest_result = None

    async def watchdog(self, session):
        while session.id in self.sessions:
            await asyncio.sleep(2)
            # Limpia ofertas abandonadas y fuentes sin frames durante 30 segundos.
            if time.monotonic() - (session.last_frame or session.created) > 30:
                await self.log_event("source_timeout", "Fuente sin frames durante 30 segundos",
                                     session=session, level="WARNING")
                await self.close(session.id)
                return

    async def process_windows(self, session):
        while session.id in self.sessions:
            number, samples = await session.buffer.queue.get()
            session.processing = True
            started = time.monotonic()
            stage = "inference"
            inference_ms = None
            window_id = f"{session.id}:{number}"
            try:
                def execute():
                    if self.predict_frames is not None:
                        return self.predict_frames([sample[0] for sample in samples], samples[-1][1])
                    frames = prepare_frames([sample[0] for sample in samples], self.config)
                    return self.predict_window(frames, samples[-1][1])
                result = await asyncio.to_thread(execute)
                inference_ms = round((time.monotonic() - started) * 1000, 3)
                detection = None
                if self.detection_store is not None:
                    stage = "storage"
                    detection = await asyncio.to_thread(
                        self.detection_store.save, result, session.source_id,
                        session_id=session.id, window_id=f"{session.id}:{number}")
                stage = "policy"
                await self.log_event("inference_completed", "Inferencia de ventana completada",
                    session=session, window_id=window_id, inference_ms=inference_ms,
                    processing_ms=round((time.monotonic() - started)*1000, 3),
                    detection_id=detection["id"] if detection else None)
                if session.id not in self.sessions:
                    return
                if session.alert_policy is not None:
                    epoch = samples[0][2] if len(samples[0]) > 2 else session.buffer.resets
                    if session.pending_alert_interruption:
                        await self.interrupt_alert(session, "timeline_reset")
                        session.pending_alert_interruption = False
                    # No confirmar alertas como consecutivas si se perdió una ventana.
                    if epoch != session.policy_epoch or number != session.last_policy_window + 1:
                        await self.interrupt_alert(session, "timeline_reset" if epoch != session.policy_epoch else "window_gap")
                        session.alert_policy.reset()
                    if epoch != session.buffer.resets:
                        # La fuente cambió su línea temporal mientras se infería.
                        await self.log_event("window_discarded", "Resultado descartado por cambio de línea temporal",
                            session=session, window_id=window_id, level="WARNING",
                            detection_id=detection["id"] if detection else None)
                        continue
                    result = session.alert_policy.update(result, samples[-1][1])
                    if self.alert_store is not None and detection is not None:
                        stage = "alert_storage"
                        event = await asyncio.to_thread(self.alert_store.apply, result, detection)
                        result["alert"]["id"] = event["id"] if event else None
                        result["alert"]["status"] = event["status"] if event else None
                        if result["alert"]["started"] or result["alert"]["ended"]:
                            await self.log_event("alert_opened" if result["alert"]["started"] else "alert_closed",
                                "Evento confirmado" if result["alert"]["started"] else "Regreso a Normal confirmado",
                                session=session, window_id=window_id, detection_id=detection["id"])
                    session.policy_epoch = epoch
                    session.last_policy_window = number
                session.latest_result = {
                    **result, "session_id": session.id, "source_id": session.source_id,
                    **({"detection_id": detection["id"]} if detection else {}),
                    "window_id": f"{session.id}:{number}",
                    "window_start": samples[0][1], "window_end": samples[-1][1],
                    "frame_count": len(samples),
                    "processing_ms": round((time.monotonic() - started) * 1000, 3)}
                session.windows_processed += 1
                session.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                if session.alert_policy is not None:
                    session.alert_policy.reset()
                    session.last_policy_window = 0
                    try:
                        await self.interrupt_alert(session, "processing_error")
                    except Exception:
                        logger.exception("No se pudo interrumpir el evento tras el fallo")
                session.processing_errors += 1
                session.last_error = "No se pudo procesar la ventana"
                logger.exception("Error procesando ventana de %s", session.id)
                await self.log_event("alert_storage_error" if stage == "alert_storage" else
                    "detection_storage_error" if stage == "storage" else "inference_error",
                    "No se pudo almacenar la alerta" if stage == "alert_storage" else
                    "No se pudo almacenar la detección" if stage == "storage" else "No se pudo procesar la ventana",
                    session=session, level="ERROR", window_id=window_id, inference_ms=inference_ms,
                    processing_ms=round((time.monotonic()-started)*1000, 3))
            finally:
                session.processing = False
                session.buffer.queue.task_done()

    async def offer(self, offer: Offer):
        async with self.lock:
            if self.sessions:
                raise HTTPException(409, "Ya hay una fuente activa; cierre su sesión primero")
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            session = Session(pc=pc, source_id=offer.source_id,
                              buffer=self.buffer_factory(self.config),
                              alert_policy=self.policy_factory() if self.policy_factory else None)
            self.sessions[session.id] = session

        tracks = []

        @pc.on("track")
        def on_track(track):
            tracks.append(track)

        @pc.on("connectionstatechange")
        async def on_state():
            if pc.connectionState in ("failed", "closed"):
                await self.close(session.id)

        try:
            async with asyncio.timeout(15):
                await pc.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
                if len(tracks) != 1 or tracks[0].kind != "video":
                    raise ValueError("Se requiere una única pista de video, sin audio")
                await pc.setLocalDescription(await pc.createAnswer())
            session.source_track = tracks[0]
            session.processing_track = session.relay.subscribe(tracks[0], buffered=False)
            self.spawn(session, self.consume(session, session.processing_track))
            self.spawn(session, self.process_windows(session))
            self.spawn(session, self.watchdog(session))
            return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
                    "session_id": session.id, "source_id": session.source_id}
        except asyncio.CancelledError:
            await self.close(session.id)
            raise
        except Exception as exc:
            await self.close(session.id)
            logger.info("Oferta WebRTC rechazada: %s", type(exc).__name__)
            await self.log_event("source_error", "Oferta WebRTC rechazada", session=session, level="ERROR")
            raise HTTPException(400, "Oferta inválida: envíe una pista de video y SDP con candidatos ICE completos") from None
