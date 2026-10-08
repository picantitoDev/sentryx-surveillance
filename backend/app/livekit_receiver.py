"""Suscriptor LiveKit de una fuente; reutiliza el pipeline y la API de resultados."""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from fractions import Fraction
from urllib.parse import urlparse

import jwt
import numpy as np
from av import VideoFrame
from aiortc.mediastreams import MediaStreamError
from fastapi import APIRouter
from livekit import api, rtc

from app.webrtc import WebRTCReceiver, Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveKitSettings:
    url: str = ""
    room: str = ""
    participant_identity: str = ""
    source_id: str = ""
    backend_identity: str = "sentrix-inference"
    token: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)

    def __post_init__(self):
        if self.url:
            parsed = urlparse(self.url)
            if (parsed.scheme not in ("ws", "wss") or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("LIVEKIT_URL debe ser ws:// o wss:// sin credenciales ni query")
        for name in ("room", "participant_identity", "source_id", "backend_identity"):
            value = getattr(self, name)
            if len(value) > 100 or any(ord(char) < 32 for char in value):
                raise ValueError(f"Configuración LiveKit inválida: {name}")
        if self.participant_identity and self.participant_identity == self.backend_identity:
            raise ValueError("La identidad del backend debe diferir de la del emisor")

    @classmethod
    def from_env(cls):
        return cls(url=os.getenv("LIVEKIT_URL", "").strip(),
                   room=os.getenv("LIVEKIT_ROOM", "").strip(),
                   participant_identity=os.getenv("LIVEKIT_PARTICIPANT_IDENTITY", "").strip(),
                   source_id=os.getenv("LIVEKIT_SOURCE_ID", "").strip(),
                   backend_identity=os.getenv("LIVEKIT_BACKEND_IDENTITY", "sentrix-inference").strip(),
                   token=os.getenv("LIVEKIT_TOKEN", "").strip(),
                   api_key=os.getenv("LIVEKIT_API_KEY", "").strip(),
                   api_secret=os.getenv("LIVEKIT_API_SECRET", "").strip())

    @property
    def missing(self):
        missing = [name for name, value in (("LIVEKIT_URL", self.url), ("LIVEKIT_ROOM", self.room),
                    ("LIVEKIT_PARTICIPANT_IDENTITY", self.participant_identity),
                    ("LIVEKIT_SOURCE_ID", self.source_id)) if not value]
        if not self.token and not (self.api_key and self.api_secret):
            missing.append("LIVEKIT_TOKEN o LIVEKIT_API_KEY + LIVEKIT_API_SECRET")
        if not self.token and not self.backend_identity:
            missing.append("LIVEKIT_BACKEND_IDENTITY")
        return missing

    def access_token(self):
        if self.token:
            # El servidor valida la firma. Esta inspección local comprueba destino/permisos
            # y expiración; no pretende autenticar un token sin conocer la clave.
            try:
                claims = jwt.decode(self.token, options={"verify_signature": False,
                                                        "verify_exp": True, "require": ["exp", "sub"]})
                grants = claims.get("video", {})
                if (grants.get("room") != self.room or grants.get("roomJoin") is not True
                        or grants.get("canSubscribe") is not True
                        or grants.get("canPublish", True) is not False
                        or grants.get("canPublishData", True) is not False
                        or claims["sub"] == self.participant_identity):
                    raise ValueError("Permisos incompatibles")
            except Exception:
                raise ValueError("LIVEKIT_TOKEN inválido: revisar expiración, sala e identidad de suscriptor") from None
            return self.token
        if not self.api_key or not self.api_secret or not self.backend_identity:
            raise ValueError("Faltan credenciales o identidad del backend LiveKit")
        return (api.AccessToken(self.api_key, self.api_secret)
                .with_identity(self.backend_identity)
                .with_ttl(timedelta(hours=1))
                .with_grants(api.VideoGrants(room_join=True, room=self.room,
                    can_subscribe=True, can_publish=False, can_publish_data=False)).to_jwt())


class LiveKitConnection:
    """Estado de una fuente, independiente del estado de conexión de la sala."""
    connectionState = "connecting"
    iceConnectionState = "livekit"

    async def close(self):
        self.connectionState = "closed"


class LiveKitFrameTrack:
    def __init__(self, stream):
        self.stream = stream
        self.origin_us = None
        self.last_us = None
        self.closed = False

    async def recv(self):
        if self.closed:
            raise MediaStreamError
        try:
            event = await self.stream.__anext__()
        except StopAsyncIteration:
            raise MediaStreamError from None
        image = event.frame
        if image.type == rtc.VideoBufferType.RGB24:
            pixels = np.frombuffer(image.data, dtype=np.uint8).reshape(image.height, image.width, 3)
        elif image.type == rtc.VideoBufferType.BGRA:
            pixels = np.frombuffer(image.data, dtype=np.uint8).reshape(image.height, image.width, 4)[:, :, 2::-1]
        else:
            # El SDK garantiza la conversión I420 -> RGBA; no todos los formatos
            # admiten conversión directa a RGB24 en la biblioteca nativa.
            rgba = image if image.type == rtc.VideoBufferType.RGBA else image.convert(rtc.VideoBufferType.RGBA)
            pixels = np.frombuffer(rgba.data, dtype=np.uint8).reshape(rgba.height, rgba.width, 4)[:, :, :3]
        # rotación del frame recibida por LiveKit; normalizar antes de VideoMAE.
        rotation = int(event.rotation)
        # SDK: los enums 1,2,3 representan 90,180,270 grados.
        if rotation in (1, 2, 3):
            pixels = np.rot90(pixels, k=-rotation)
        frame = VideoFrame.from_ndarray(np.ascontiguousarray(pixels), format="rgb24")
        timestamp_us = int(event.timestamp_us)
        if self.origin_us is None or (self.last_us is not None and timestamp_us < self.last_us):
            self.origin_us = timestamp_us
        self.last_us = timestamp_us
        frame.pts = timestamp_us - self.origin_us
        frame.time_base = Fraction(1, 1_000_000)
        return frame

    def stop(self):
        self.closed = True

    async def aclose(self):
        self.stop()
        await self.stream.aclose()


@dataclass
class LiveKitSession(Session):
    room_name: str = ""
    participant_identity: str = ""
    track_sid: str = ""
    stream_generation: int = 0

    def snapshot(self):
        return {**super().snapshot(), "transport": "livekit", "room": self.room_name,
                "participant_identity": self.participant_identity, "track_sid": self.track_sid}


class LiveKitReceiver(WebRTCReceiver):
    def __init__(self, *args, settings=None, room_factory=None, stream_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = settings or LiveKitSettings.from_env()
        self.room_factory = room_factory or rtc.Room
        self.stream_factory = stream_factory or (
            lambda track: rtc.VideoStream(track, format=rtc.VideoBufferType.RGBA, capacity=2))
        self.room = None
        self.room_connected = False
        self.state = "missing_configuration" if self.settings.missing else "stopped"
        self.last_connection_error = None
        self.retry_count = 0
        self._runner = None
        self._closing = False
        self._generation = 0
        self._events = set()
        self._reconcile_lock = asyncio.Lock()
        self.router = APIRouter(tags=["LiveKit / inferencia"])
        self.router.add_api_route("/webrtc/sessions", self.list_sessions, methods=["GET"])
        self.router.add_api_route("/webrtc/sessions/{session_id}", self.status, methods=["GET"])
        self.router.add_api_route("/livekit/status", self.connection_status, methods=["GET"])

    def connection_status(self):
        return {"transport": "livekit", "configured": not self.settings.missing,
                "state": self.state, "room_connected": self.room_connected,
                "room": self.settings.room or None,
                "participant_identity": self.settings.participant_identity or None,
                "source_id": self.settings.source_id or None,
                "missing_configuration": self.settings.missing,
                "retry_count": self.retry_count, "last_connection_error": self.last_connection_error}

    async def start(self):
        if self.settings.missing:
            self.state = "missing_configuration"
            await self.log_event("configuration_missing", "Configuración de LiveKit incompleta", level="WARNING")
            return
        if self._runner is None:
            self._closing = False
            self._runner = asyncio.create_task(self._run())

    def _schedule_reconcile(self, room):
        if self._closing or room is not self.room:
            return
        task = asyncio.create_task(self._reconcile(room))
        self._events.add(task)
        def done(completed):
            self._events.discard(completed)
            if not completed.cancelled() and completed.exception():
                self.last_connection_error = "No se pudo preparar la pista LiveKit"
                self.state = "error"
                logger.error("Falló la preparación de pista LiveKit (%s)", type(completed.exception()).__name__)
        task.add_done_callback(done)

    def _bind_events(self, room, disconnected):
        for event in ("track_published", "track_unpublished", "track_subscribed", "track_unsubscribed",
                      "participant_connected", "participant_disconnected", "track_muted", "track_unmuted"):
            room.on(event, lambda *args: self._schedule_reconcile(room))

        @room.on("reconnecting")
        def reconnecting():
            if room is self.room:
                self.room_connected = False
                self._generation += 1
                self.state = "reconnecting"
                for session in self.sessions.values():
                    session.pc.connectionState = "reconnecting"
                    session.latest_result = None
                self._schedule_reconcile(room)

        @room.on("reconnected")
        def reconnected():
            if room is self.room:
                self.room_connected = True
                self._schedule_reconcile(room)

        @room.on("disconnected")
        def on_disconnected(*args):
            if room is self.room:
                self.room_connected = False
                self._generation += 1
                self.state = "disconnected"
                for session in self.sessions.values():
                    session.pc.connectionState = "disconnected"
                    session.latest_result = None
                disconnected.set()

        @room.on("track_subscription_failed")
        def subscription_failed(participant, track_sid, error):
            if room is self.room and participant.identity == self.settings.participant_identity:
                self.last_connection_error = "LiveKit rechazó la suscripción de video"
                self.state = "subscription_error"

    async def _run(self):
        delay = 2
        while not self._closing:
            room = None
            self.state = "connecting"
            try:
                room = self.room_factory()
                self.room = room
                self._generation += 1
                disconnected = asyncio.Event()
                self._bind_events(room, disconnected)
                token = self.settings.access_token()
                async with asyncio.timeout(20):
                    await room.connect(self.settings.url, token,
                                       options=rtc.RoomOptions(auto_subscribe=False))
                self.room_connected = True
                self.last_connection_error = None
                self.state = "waiting_publisher"
                await self.log_event("transport_connected", "Conexión a LiveKit establecida")
                delay = 2
                await self._reconcile(room)
                while not disconnected.is_set():
                    try:
                        await asyncio.wait_for(disconnected.wait(), timeout=2)
                    except TimeoutError:
                        await self._reconcile(room)
                await self.log_event("transport_disconnected", "Conexión a LiveKit interrumpida", level="WARNING")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # No incluir mensajes del SDK: pueden contener URL o datos de acceso.
                self.last_connection_error = f"No se pudo conectar a LiveKit ({type(error).__name__})"
                self.state = "connection_error"
                logger.warning("Conexión LiveKit fallida (%s)", type(error).__name__)
                await self.log_event("transport_error", "No se pudo conectar a LiveKit", level="ERROR")
            finally:
                self.room_connected = False
                self.room = None
                for session_id in list(self.sessions):
                    await self.close(session_id)
                if room is not None:
                    try:
                        async with asyncio.timeout(5):
                            await room.disconnect()
                    except Exception:
                        logger.warning("No se completó el cierre de la sala LiveKit")
            if not self._closing:
                self.retry_count += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _reconcile(self, room):
        async with self._reconcile_lock:
            if self._closing or room is not self.room:
                return
            if not self.room_connected:
                for session_id in list(self.sessions):
                    await self.close(session_id)
                return
            participant = next((item for item in room.remote_participants.values()
                                if item.identity == self.settings.participant_identity), None)
            publications = [] if participant is None else [publication for publication in
                participant.track_publications.values() if publication.kind == rtc.TrackKind.KIND_VIDEO]
            if len(publications) != 1:
                for session_id in list(self.sessions):
                    await self.close(session_id)
                for publication in publications:
                    publication.set_subscribed(False)
                if len(publications) > 1:
                    self.state = "ambiguous_video"
                    self.last_connection_error = "El emisor debe publicar una sola pista de video"
                else:
                    self.state = "waiting_publisher" if participant is None else "waiting_video"
                    self.last_connection_error = None
                return
            publication = publications[0]
            if not publication.subscribed:
                publication.set_subscribed(True)
            if publication.track is None or publication.muted:
                for session_id in list(self.sessions):
                    await self.close(session_id)
                self.state = "waiting_video"
                return
            for session in self.sessions.values():
                if (session.track_sid == publication.sid
                        and session.stream_generation == self._generation):
                    return
            for session_id in list(self.sessions):
                await self.close(session_id)
            if self._closing or room is not self.room or not self.room_connected:
                return
            stream = self.stream_factory(publication.track)
            session = LiveKitSession(pc=LiveKitConnection(), source_id=self.settings.source_id,
                buffer=self.buffer_factory(self.config),
                alert_policy=self.policy_factory() if self.policy_factory else None,
                room_name=self.settings.room, participant_identity=participant.identity,
                track_sid=publication.sid, stream_generation=self._generation,
                processing_track=LiveKitFrameTrack(stream))
            self.sessions[session.id] = session
            self.state = "waiting_frames"
            self.last_connection_error = None
            self.spawn(session, self.consume(session, session.processing_track))
            self.spawn(session, self.process_windows(session))
            self.spawn(session, self.watchdog(session))

    def accept_frame(self, session, frame):
        if (not self.room_connected or session.id not in self.sessions
                or session.stream_generation != self._generation):
            return
        super().accept_frame(session, frame)
        session.pc.connectionState = "connected"
        self.state = "receiving"

    async def close(self, session_id):
        session = self.sessions.get(session_id)
        await super().close(session_id)
        if session is not None:
            try:
                async with asyncio.timeout(5):
                    await session.processing_track.aclose()
            except Exception:
                logger.warning("No se completó el cierre del stream LiveKit")
            if not self.sessions and self.room_connected and not self._closing:
                self.state = "waiting_video"

    async def shutdown(self):
        self._closing = True
        if self._runner is not None:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
            self._runner = None
        pending = list(self._events)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await super().shutdown()
        self.room_connected = False
        self.state = "stopped"
