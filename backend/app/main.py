"""API SentriX: VideoMAE CUDA por defecto y simulador explícito para pruebas."""
import logging
import asyncio
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from app.webrtc import WebRTCReceiver
from typing import Annotated, Literal, Protocol
from fastapi import FastAPI, HTTPException, Query, Response
from app.detections import DetectionStore
from app.inference_logs import InferenceLogStore, logs_router
from app.alerts import AlertStore, EventPolicy, alerts_router
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Pixel = Annotated[FiniteNumber, Field(ge=0, le=1)]
Classes = Literal["Normal", "Hurto", "Robo", "Agresión física", "Vandalismo"]

class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: Annotated[FiniteNumber, Field(ge=0)]
    source_id: str = Field(default="camara_mari", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    frames: list[list[list[list[Pixel]]]] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def check_shape(self):
        # Contrato provisional THWC: tiempo, alto, ancho, canales RGB.
        height = len(self.frames[0])
        width = len(self.frames[0][0]) if height else 0
        if not 1 <= height <= 224 or not 1 <= width <= 224:
            raise ValueError("Alto y ancho deben estar entre 1 y 224")
        for frame in self.frames:
            if len(frame) != height:
                raise ValueError("Todos los frames deben tener el mismo alto")
            for row in frame:
                if len(row) != width:
                    raise ValueError("Todas las filas deben tener el mismo ancho")
                if any(len(pixel) != 3 for pixel in row):
                    raise ValueError("Cada pixel debe contener tres canales RGB")
        return self

class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clase: Classes
    confianza: Annotated[FiniteNumber, Field(ge=0, le=1)]
    timestamp: Annotated[FiniteNumber, Field(ge=0)]
    simulated: bool
    probabilities: dict[str, Annotated[FiniteNumber, Field(ge=0, le=1)]] | None = None

class Predictor(Protocol):
    def predict(self, window: Window) -> Prediction: ...

class MockPredictor:
    def predict(self, window: Window) -> Prediction:
        # Fixture fija para integración. No representa una detección real.
        return Prediction(clase="Hurto", confianza=0.75,
                          timestamp=window.timestamp, simulated=True)

class BodyLimit:
    """Limita el cuerpo antes de que FastAPI deserialice el JSON."""
    def __init__(self, app, limit=2 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        parts, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                from starlette.responses import JSONResponse
                return await JSONResponse(status_code=413,
                    content={"detail": "La solicitud supera 2 MiB"})(scope, receive, send)
            parts.append(body)
            if not message.get("more_body", False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(parts), "more_body": False}
        await self.app(scope, replay, send)

def create_app(predictor: Predictor | None = None, *, mode: str | None = None,
               transport: str | None = None, livekit_settings=None, detection_db=None, log_db=None):
    database_url = os.getenv("SENTRIX_DATABASE_URL")
    if not database_url and not (detection_db or os.getenv("SENTRIX_DETECTION_DB")):
        raise ValueError("Configurar SENTRIX_DATABASE_URL con una conexión PostgreSQL")
    store = DetectionStore(detection_db or database_url or os.getenv("SENTRIX_DETECTION_DB"))
    log_store = InferenceLogStore(log_db or database_url or os.getenv("SENTRIX_LOG_DB") or store.path)
    alert_store = AlertStore(store.path)
    event_policy = EventPolicy.from_env()
    selected_mode = mode or os.getenv("SENTRIX_PREDICTOR", "videomae")
    real_mode = predictor is None and selected_mode == "videomae"
    if predictor is None and selected_mode not in ("videomae", "mock"):
        raise ValueError("SENTRIX_PREDICTOR debe ser videomae o mock")
    selected_transport = transport or os.getenv("SENTRIX_VIDEO_TRANSPORT", "livekit")
    if selected_transport not in ("livekit", "webrtc"):
        raise ValueError("SENTRIX_VIDEO_TRANSPORT debe ser livekit o webrtc")
    receiver_type = WebRTCReceiver
    receiver_options = {"detection_store": store, "log_store": log_store, "alert_store": alert_store}
    policy_factory = lambda: EventPolicy(event_policy.thresholds)
    if selected_transport == "livekit":
        from app.livekit_receiver import LiveKitReceiver
        receiver_type = LiveKitReceiver
        receiver_options["settings"] = livekit_settings
    def predict_window(frames, timestamp):
        window = Window(frames=frames, timestamp=timestamp)
        return Prediction.model_validate(app.state.predictor.predict(window)).model_dump(exclude_none=True)
    if real_mode:
        from app.videomae import VideoMAEPredictor, load_metadata, config_from_metadata
        from app.pipeline import VideoMAEBuffer
        receiver = receiver_type(
            predict_window, config_from_metadata(load_metadata()), buffer_factory=VideoMAEBuffer,
            predict_frames=lambda frames, timestamp: Prediction.model_validate(
                app.state.predictor.predict_frames(frames, timestamp)).model_dump(exclude_none=True),
            policy_factory=policy_factory, **receiver_options)
    else:
        receiver = receiver_type(predict_window, policy_factory=policy_factory, **receiver_options)
    @asynccontextmanager
    async def lifespan(app):
        if database_url:
            # Fallar al iniciar si la conexión principal no está disponible.
            from app.users import UserStore
            def initialize_storage():
                for persistent_store in (store, alert_store, log_store, UserStore(store.path)):
                    connection = persistent_store.connect()
                    try:
                        with connection:
                            connection.execute("SELECT 1")
                    finally:
                        connection.close()
            await asyncio.to_thread(initialize_storage)
        try:
            try:
                await asyncio.to_thread(alert_store.interrupt, reason="service_restarted")
            except Exception:
                logger.exception("No se pudieron recuperar las alertas pendientes")
            if real_mode:
                started = time.perf_counter()
                await asyncio.to_thread(log_store.emit, "model_loading", "Iniciando carga de VideoMAE")
                try:
                    provider = os.getenv("SENTRIX_PROVIDER", "cuda")
                    if provider not in ("cuda", "cpu"):
                        raise ValueError("SENTRIX_PROVIDER debe ser cuda o cpu; no hay fallback automático")
                    logger.info("Cargando VideoMAE (%s); esperar antes de conectar WebRTC", provider)
                    app.state.predictor = await asyncio.to_thread(VideoMAEPredictor, provider=provider)
                except Exception:
                    await asyncio.to_thread(log_store.emit, "model_error", "No se pudo cargar el modelo",
                                            level="ERROR", processing_ms=round((time.perf_counter()-started)*1000, 3))
                    raise
                logger.info("Modelo listo: %s", app.state.predictor.health())
                await asyncio.to_thread(log_store.emit, "model_ready", "Modelo cargado y calentamiento completado",
                                        processing_ms=round((time.perf_counter()-started)*1000, 3))
            if selected_transport == "livekit":
                await receiver.start()
            await asyncio.to_thread(log_store.emit, "service_started", "Servicio de inferencia iniciado")
            yield
        finally:
            await receiver.shutdown()
            await asyncio.to_thread(log_store.emit, "service_stopped", "Servicio de inferencia detenido")
            if real_mode:
                app.state.predictor = None
    app = FastAPI(title="SentriX · Servicio de inferencia", version="0.6.0", lifespan=lifespan,
                  description="VideoMAE CUDA; recepción LiveKit y consulta de resultados HTTP.")
    app.add_middleware(BodyLimit)
    origins = [value.strip() for value in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if value.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"],
                       expose_headers=["X-Detection-ID"])
    from app.users import users_router
    app.include_router(users_router(store.path))
    app.include_router(receiver.router)
    app.include_router(logs_router(log_store))
    app.include_router(alerts_router(alert_store))
    app.state.webrtc = receiver
    app.state.video_receiver = receiver
    app.state.detection_store = store
    app.state.log_store = log_store
    app.state.alert_store = alert_store
    app.state.predictor = predictor if predictor is not None else (None if real_mode else MockPredictor())

    @app.get("/health")
    def health():
        video_status = {"video_transport": selected_transport}
        if selected_transport == "livekit":
            video_status["livekit"] = receiver.connection_status()
        if real_mode:
            if app.state.predictor is None:
                raise HTTPException(503, "Modelo no disponible")
            return {"status": "ok", "predictor": type(app.state.predictor).__name__,
                    **app.state.predictor.health(), **video_status}
        return {"status": "ok", "predictor": type(app.state.predictor).__name__,
                "model_loaded": False if isinstance(app.state.predictor, MockPredictor) else None,
                **video_status}

    @app.post("/predict", response_model=Prediction, response_model_exclude_none=True)
    def predict(window: Window, response: Response):
        if real_mode and len(window.frames) != 16:
            raise HTTPException(422, "VideoMAE requiere exactamente 16 frames muestreados; para video use WebRTC")
        started = time.perf_counter()
        try:
            result = app.state.predictor.predict(window)
            prediction = Prediction.model_validate(result)
            inference_ms = round((time.perf_counter() - started) * 1000, 3)
        except Exception:
            logger.exception("Error al ejecutar el predictor")
            log_store.emit("inference_error", "No se pudo ejecutar la inferencia HTTP", level="ERROR",
                           camera=window.source_id, processing_ms=round((time.perf_counter()-started)*1000, 3))
            raise HTTPException(status_code=503, detail="Predictor no disponible") from None
        try:
            detection = store.save(prediction.model_dump(exclude_none=True), window.source_id)
        except Exception:
            logger.exception("Error almacenando detección")
            log_store.emit("detection_storage_error", "No se pudo almacenar la detección HTTP", level="ERROR",
                           camera=window.source_id, inference_ms=inference_ms,
                           processing_ms=round((time.perf_counter()-started)*1000, 3))
            raise HTTPException(503, "Almacenamiento de detecciones no disponible") from None
        response.headers["X-Detection-ID"] = detection["id"]
        log_store.emit("inference_completed", "Inferencia HTTP completada y detección almacenada",
                       camera=window.source_id, detection_id=detection["id"], inference_ms=inference_ms,
                       processing_ms=round((time.perf_counter()-started)*1000, 3))
        return prediction

    @app.get("/detections")
    def list_detections(camera: str | None = None, limit: int = Query(100, ge=1, le=500),
                        offset: int = Query(0, ge=0)):
        try:
            return store.list(camera, limit, offset)
        except Exception:
            logger.exception("Error consultando detecciones")
            raise HTTPException(503, "Almacenamiento de detecciones no disponible") from None

    @app.get("/detections/{detection_id}")
    def get_detection(detection_id: str):
        try:
            detection = store.get(detection_id)
        except Exception:
            logger.exception("Error consultando detección")
            raise HTTPException(503, "Almacenamiento de detecciones no disponible") from None
        if detection is None:
            raise HTTPException(404, "Detección no encontrada")
        return detection
    return app

app = create_app()
