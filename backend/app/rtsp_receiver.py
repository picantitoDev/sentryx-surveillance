"""Fuentes RTSP/RTSPS independientes para el pipeline EN-3/EN-4 existente.

Este modulo REUTILIZA WebRTCReceiver.process_windows para preservar
VideoMAEBuffer, inferencia ONNX, almacenamiento y la politica de alertas.
Las conexiones se resuelven desde el registro privado de cámaras.
"""
import asyncio
import hashlib
import logging
import os
import secrets
import socket
import ssl
import time
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import quote

import av
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from app.cameras import CameraRegistration, CameraStore

from app.webrtc import Session, WebRTCReceiver

logger = logging.getLogger(__name__)


class StartSource(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [{"source_id": "camara1", "stream_path": "camera01"}]})
    source_id: str = Field(default="camara1", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    stream_path: str | None = Field(default=None, max_length=256,
                                  pattern=r"^[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*$")


class _RTSPPeer:
    """Adaptador minimo para Session.snapshot y WebRTCReceiver.close."""
    connectionState = "connected"
    iceConnectionState = "connected"

    async def close(self):
        self.connectionState = "closed"
        self.iceConnectionState = "closed"


def _verify_control_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    expected = os.getenv("SENTRIX_CONTROL_API_KEY", "")
    if not expected or not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="API key invalida")


def _next_frame(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None


async def _blocking(executor, function, *args, on_cancel=None, **kwargs):
    """Esperar operaciones nativas antes de cerrar; cancelar no detiene FFmpeg."""
    future = asyncio.get_running_loop().run_in_executor(
        executor, partial(function, *args, **kwargs))
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Los timeouts de apertura/lectura limitan la espera del cierre.
        try:
            result = await future
            if on_cancel is not None:
                await asyncio.get_running_loop().run_in_executor(
                    executor, partial(on_cancel, result))
        except Exception:
            pass
        raise


def _verify_rtsps_fingerprint(host: str, port: int, expected: str):
    """Comprobacion previa de certificado. No sustituye TLS verificado extremo a extremo."""
    expected = expected.replace(":", "").upper().strip()
    if len(expected) != 64 or any(c not in "0123456789ABCDEF" for c in expected):
        raise ValueError("SENTRIX_RTSP_SHA256 debe ser un SHA-256 hexadecimal")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=5) as tcp:
        with context.wrap_socket(tcp, server_hostname=host) as tls:
            observed = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest().upper()
    if not secrets.compare_digest(observed, expected):
        raise RuntimeError("Certificado RTSPS no coincide con la huella configurada")


class RTSPReceiver(WebRTCReceiver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.protocol = os.getenv("SENTRIX_RTSP_PROTOCOL", "rtsp").strip().lower()
        self.host = os.environ.get("SENTRIX_RTSP_HOST", "184.194.94.182").strip()
        self.port = int(os.environ.get("SENTRIX_RTSP_PORT", "8554"))
        self.user = os.environ.get("SENTRIX_RTSP_USER", "viewer")
        self.password = os.environ.get("SENTRIX_RTSP_PASSWORD", "")
        self.fingerprint = os.environ.get("SENTRIX_RTSP_SHA256", "")
        if self.protocol not in ("rtsp", "rtsps"):
            raise ValueError("SENTRIX_RTSP_PROTOCOL debe ser rtsp o rtsps")
        if not os.getenv("SENTRIX_CONTROL_API_KEY"):
            raise ValueError("Configurar SENTRIX_CONTROL_API_KEY")
        if self.protocol == "rtsps" and not (self.password and self.fingerprint):
            raise ValueError("RTSPS requiere SENTRIX_RTSP_PASSWORD y SENTRIX_RTSP_SHA256")
        # La dirección viene de configuración privada, nunca del cuerpo HTTP.
        ipaddress.IPv4Address(self.host)
        if not 1 <= self.port <= 65535:
            raise ValueError("SENTRIX_RTSP_PORT debe estar entre 1 y 65535")
        normalized = self.fingerprint.replace(":", "").strip().upper()
        if self.protocol == "rtsps" and (len(normalized) != 64 or any(c not in "0123456789ABCDEF" for c in normalized)):
            raise ValueError("SENTRIX_RTSP_SHA256 debe ser un SHA-256 hexadecimal")
        self.default_camera = CameraRegistration(
            source_id=os.getenv("SENTRIX_SOURCE_ID", "camara1"),
            name=os.getenv("SENTRIX_CAMERA_NAME", "Gabriel"),
            stream_path=os.getenv("SENTRIX_STREAM_PATH", "camera01"),
            protocol=self.protocol, host=self.host, port=self.port, user=self.user,
            password=self.password, fingerprint=self.fingerprint)
        self.camera_store = CameraStore(self.detection_store.path)
        # Solo exponer las nuevas operaciones; no publicar /webrtc/offer.
        self.router = APIRouter(prefix="/sources", tags=["Fuentes RTSP/RTSPS"],
                                dependencies=[Depends(_verify_control_key)])
        self.router.add_api_route(
            "/start", self.start_source, methods=["POST"],
            summary="Iniciar analisis de una camara de MediaMTX",
            description=("El panel envia source_id y stream_path; no envia video ni credenciales. "
                         "Una fuente nueva se registra con la conexion privada de MediaMTX. "
                         "FastAPI consume directamente el stream RTSP/RTSPS. IDs distintos con la misma "
                         "ruta analizan el mismo video. Para cambiar una ruta activa, detener primero "
                         "la fuente con DELETE /sources/{source_id}."))
        self.router.add_api_route("", self.list_sources, methods=["GET"])
        self.router.add_api_route("/{source_id}/status", self.source_status, methods=["GET"])
        self.router.add_api_route("/{source_id}", self.stop_source, methods=["DELETE"])
        self.camera_router = APIRouter(prefix="/cameras", tags=["Camaras"],
                                       dependencies=[Depends(_verify_control_key)])
        self.camera_router.add_api_route("", self.list_cameras, methods=["GET"])
        self.camera_router.add_api_route("", self.register_camera, methods=["POST"], status_code=201)
        self.camera_router.add_api_route("/{source_id}", self.get_camera, methods=["GET"])
        self.camera_router.add_api_route("/{source_id}", self.update_camera, methods=["PUT"])
        self.camera_router.add_api_route("/{source_id}", self.delete_camera, methods=["DELETE"])

    def _url(self, camera=None):
        camera = camera or self.default_camera
        # Las credenciales solo se usan dentro de FastAPI; no se registran en logs.
        credentials = ""
        if camera.password.get_secret_value():
            credentials = f"{quote(camera.user, safe='')}:{quote(camera.password.get_secret_value(), safe='')}@"
        return f"{camera.protocol}://{credentials}{camera.host}:{camera.port}/{camera.stream_path}"

    async def _storage(self, method, *args, **kwargs):
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except Exception:
            raise HTTPException(503, "Registro de camaras no disponible") from None

    async def initialize_cameras(self):
        await self._storage(self.camera_store.save, self.default_camera)

    async def _camera(self, source_id):
        camera = await self._storage(self.camera_store.get, source_id)
        if camera is None:
            raise HTTPException(404, "Camara no registrada")
        return camera

    def _active(self, source_id):
        return next((s for s in self.sessions.values() if s.source_id == source_id), None)

    async def list_cameras(self):
        cameras = await self._storage(self.camera_store.list)
        return [{**c.public_config(), "active": self._active(c.source_id) is not None} for c in cameras]

    async def get_camera(self, source_id: str):
        camera = await self._camera(source_id)
        return {**camera.public_config(), "active": self._active(source_id) is not None}

    def _resolve_camera(self, body, base):
        values = base.private_config()
        values.update(body.model_dump(exclude_unset=True, exclude={"password"}))
        if "password" in body.model_fields_set:
            values["password"] = body.password.get_secret_value()
        try:
            camera = CameraRegistration.model_validate(values)
            if camera.protocol == "rtsps" and not camera.password.get_secret_value():
                raise ValueError("Falta password")
            return camera
        except (ValidationError, ValueError):
            raise HTTPException(422, "Configuracion RTSP/RTSPS invalida") from None

    async def register_camera(self, body: CameraRegistration):
        async with self.lock:
            camera = self._resolve_camera(body, self.default_camera)
            if "stream_path" not in body.model_fields_set:
                camera.stream_path = body.source_id
            if "name" not in body.model_fields_set:
                camera.name = body.source_id
            if not await self._storage(self.camera_store.save, camera):
                raise HTTPException(409, "Camara ya registrada")
            return camera.public_config()

    async def update_camera(self, source_id: str, body: CameraRegistration):
        async with self.lock:
            if source_id != body.source_id:
                raise HTTPException(422, "source_id no se puede cambiar")
            if self._active(source_id):
                raise HTTPException(409, "Detener la camara antes de editarla")
            camera = self._resolve_camera(body, await self._camera(source_id))
            if not await self._storage(self.camera_store.save, camera, update=True):
                raise HTTPException(404, "Camara no registrada")
            return camera.public_config()

    async def delete_camera(self, source_id: str):
        async with self.lock:
            if source_id == self.default_camera.source_id:
                raise HTTPException(409, "La camara predeterminada se puede deshabilitar, no eliminar")
            if self._active(source_id):
                raise HTTPException(409, "Detener la camara antes de eliminarla")
            if not await self._storage(self.camera_store.delete, source_id):
                raise HTTPException(404, "Camara no registrada")
            return {"source_id": source_id, "deleted": True}

    async def list_sources(self):
        return [await self.source_status(c.source_id)
                for c in await self._storage(self.camera_store.list)]

    def _snapshot(self, session):
        result = session.snapshot()
        result["status"] = getattr(session, "rtsp_status", "connecting")
        result["active"] = True
        return result

    def connection_status(self):
        session = next(iter(self.sessions.values()), None)
        return {"transport": "rtsp", "configured": True,
                "state": session.rtsp_status if session else "stopped",
                "source_id": session.source_id if session else None,
                "active_sources": [s.source_id for s in self.sessions.values()],
                "active_count": len(self.sessions)}

    async def start_source(self, body: StartSource):
        async with self.lock:
            camera = await self._storage(self.camera_store.get, body.source_id)
            if camera is None:
                if body.stream_path is None:
                    raise HTTPException(404, "Enviar stream_path para una camara nueva")
                camera = self.default_camera.model_copy(update={
                    "source_id": body.source_id, "name": body.source_id,
                    "stream_path": body.stream_path})
                if not await self._storage(self.camera_store.save, camera):
                    camera = await self._camera(body.source_id)
            if not camera.enabled:
                raise HTTPException(409, "Camara deshabilitada")
            active = self._active(body.source_id)
            if body.stream_path is not None and body.stream_path != camera.stream_path:
                if active:
                    raise HTTPException(409, "Detener la camara antes de cambiar stream_path")
                camera = camera.model_copy(update={"stream_path": body.stream_path})
                if not await self._storage(self.camera_store.save, camera, update=True):
                    raise HTTPException(404, "Camara no registrada")
            if active:
                return self._snapshot(active)  # POST idempotente por cámara
            session = Session(
                pc=_RTSPPeer(), source_id=body.source_id,
                buffer=self.buffer_factory(self.config),
                alert_policy=self.policy_factory() if self.policy_factory else None,
            )
            session.rtsp_status = "connecting"
            session.camera_config = camera
            self.sessions[session.id] = session
            self.spawn(session, self.process_windows(session))
            self.spawn(session, self._read_loop(session))
            return self._snapshot(session)

    async def source_status(self, source_id: str):
        await self._camera(source_id)
        for session in self.sessions.values():
            if session.source_id == source_id:
                return self._snapshot(session)
        return {"source_id": source_id, "active": False, "status": "stopped",
                "latest_result": None, "frames_received": 0, "windows_processed": 0}

    async def stop_source(self, source_id: str):
        async with self.lock:
            await self._camera(source_id)
            session = self._active(source_id)
            if session:
                await self.close(session.id)
                return {"source_id": source_id, "status": "stopped", "active": False}
        return {"source_id": source_id, "status": "stopped", "active": False}

    def _accept_rtsp_frame(self, session, frame, base_time):
        # frame.time puede faltar en algunos flujos RTSP; se usa reloj monotono.
        timestamp = float(frame.time) if frame.time is not None else time.monotonic() - base_time
        session.frames += 1
        session.width, session.height = frame.width, frame.height
        session.timestamp = timestamp
        session.last_frame = time.monotonic()
        previous_epoch = session.buffer.resets
        session.buffer.push(frame, timestamp)
        if session.alert_policy is not None and session.buffer.resets != previous_epoch:
            session.pending_alert_interruption = True
            session.alert_policy.reset()
            session.policy_epoch = session.buffer.resets
            session.last_policy_window = 0
            session.latest_result = None

    async def _read_loop(self, session):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentrix-rtsp")
        try:
            await self._read_connections(session, executor)
        finally:
            executor.shutdown(wait=False)

    async def _read_connections(self, session, executor):
        camera = session.camera_config
        while session.id in self.sessions:
            container = None
            try:
                # Verificacion previa de huella del certificado autofirmado de EC2.
                if camera.protocol == "rtsps":
                    await _blocking(executor, _verify_rtsps_fingerprint,
                                    camera.host, camera.port, camera.fingerprint)
                container = await _blocking(
                    executor, av.open, self._url(camera), options={"rtsp_transport": "tcp"},
                    timeout=(5.0, 5.0), on_cancel=lambda opened: opened.close())
                iterator = await _blocking(executor, lambda: iter(container.decode(video=0)))
                base_time = time.monotonic()
                while session.id in self.sessions:
                    frame = await _blocking(executor, _next_frame, iterator)
                    if session.id not in self.sessions:
                        return
                    if frame is None:
                        raise ConnectionError("Fin de stream")
                    self._accept_rtsp_frame(session, frame, base_time)
                    if session.rtsp_status != "receiving":
                        session.rtsp_status = "receiving"
                        session.pc.connectionState = "connected"
                        session.last_error = None
                        await self.log_event("source_connected", "Stream RTSPS recibido", session=session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # No registrar URL, password ni excepciones que incluyan la URL.
                logger.warning("%s temporalmente no disponible para %s: %s",
                               camera.protocol.upper(), session.source_id, type(exc).__name__)
                session.rtsp_status = "reconnecting"
                session.pc.connectionState = "disconnected"
                session.last_error = f"Error de conexion: {type(exc).__name__}"
                session.buffer.clear()
                session.buffer.last_timestamp = None
                session.buffer.next_sample = None
                session.buffer.resets += 1
                if hasattr(session.buffer, "frame_index"):
                    session.buffer.frame_index = 0
                if session.alert_policy:
                    session.alert_policy.reset()
                    session.policy_epoch = session.buffer.resets
                    session.last_policy_window = 0
                    session.pending_alert_interruption = False
                    await self.interrupt_alert(session, "rtsp_interrupted")
                session.latest_result = None
                await self.log_event("transport_error", f"Stream {camera.protocol.upper()} interrumpido; reintentando",
                                     session=session, level="WARNING")
            finally:
                if container is not None:
                    await _blocking(executor, container.close)
            if session.id in self.sessions:
                await asyncio.sleep(3)
