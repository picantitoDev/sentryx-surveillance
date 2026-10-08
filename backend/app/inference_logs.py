"""Historial operativo consultable, separado del almacenamiento de detecciones."""
import logging
from app.database import connect
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel

logger = logging.getLogger(__name__)
EventType = Literal["service_started", "service_stopped", "model_loading", "model_ready",
                    "model_error", "source_connected", "source_disconnected", "source_timeout",
                    "source_error", "transport_connected", "transport_disconnected",
                    "transport_error", "configuration_missing", "inference_completed",
                    "inference_error", "detection_storage_error", "window_discarded",
                    "alert_opened", "alert_closed", "alert_interrupted", "alert_storage_error"]


class InferenceLog(BaseModel):
    id: str
    fecha_hora: AwareDatetime
    camera: str | None
    event_type: EventType
    level: Literal["INFO", "WARNING", "ERROR"]
    message: str
    processing_ms: float | None
    inference_ms: float | None
    session_id: str | None
    window_id: str | None
    detection_id: str | None


class LogPage(BaseModel):
    items: list[InferenceLog]
    total: int
    limit: int
    offset: int


class InferenceLogStore:
    def __init__(self, path):
        self.path = str(path) if str(path).startswith(("postgresql://", "postgres://")) else Path(path)

    def connect(self):
        connection = connect(self.path)
        connection.execute("""CREATE TABLE IF NOT EXISTS inference_logs (
            id TEXT PRIMARY KEY, fecha_hora TEXT NOT NULL, camera TEXT,
            event_type TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL,
            processing_ms REAL, inference_ms REAL, session_id TEXT, window_id TEXT,
            detection_id TEXT
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS logs_date ON inference_logs(fecha_hora)")
        connection.execute("CREATE INDEX IF NOT EXISTS logs_camera_event ON inference_logs(camera, event_type)")
        return connection

    def write(self, event_type, message, *, camera=None, level="INFO", processing_ms=None,
              inference_ms=None, session_id=None, window_id=None, detection_id=None):
        record = InferenceLog(id=str(uuid4()), fecha_hora=datetime.now(timezone.utc), camera=camera,
            event_type=event_type, level=level, message=message, processing_ms=processing_ms,
            inference_ms=inference_ms, session_id=session_id, window_id=window_id,
            detection_id=detection_id).model_dump(mode="json")
        record["fecha_hora"] = datetime.fromisoformat(record["fecha_hora"]).isoformat(
            timespec="microseconds").replace("+00:00", "Z")
        connection = self.connect()
        try:
            with connection:
                connection.execute("INSERT INTO inference_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   tuple(record.values()))
        finally:
            connection.close()
        return record

    def emit(self, *args, **kwargs):
        # Un fallo de logs no debe interrumpir video ni sustituir el resultado del modelo.
        try:
            return self.write(*args, **kwargs)
        except Exception:
            logger.error("No se pudo persistir el log de inferencia", exc_info=True)
            return None

    def get(self, identifier):
        connection = self.connect()
        try:
            row = connection.execute("SELECT * FROM inference_logs WHERE id = ?", (identifier,)).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def list(self, *, camera=None, event_type=None, level=None, date_from=None, date_to=None,
             limit=100, offset=0):
        clauses, values = [], []
        for column, value in (("camera", camera), ("event_type", event_type), ("level", level)):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        for operator, value in ((">=", date_from), ("<=", date_to)):
            if value is not None:
                clauses.append(f"fecha_hora {operator} ?")
                values.append(value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        connection = self.connect()
        try:
            with connection:
                connection.execute("BEGIN")
                total = connection.execute("SELECT COUNT(*) FROM inference_logs" + where, values).fetchone()[0]
                rows = connection.execute("SELECT * FROM inference_logs" + where +
                    " ORDER BY fecha_hora DESC, id DESC LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
            return dict(items=[dict(row) for row in rows], total=total, limit=limit, offset=offset)
        finally:
            connection.close()


def logs_router(store):
    router = APIRouter(prefix="/inference-logs", tags=["Logs de inferencia"])

    @router.get("", response_model=LogPage)
    def list_logs(camera: str | None = Query(None, min_length=1, max_length=64),
                  event_type: EventType | None = None,
                  level: Literal["INFO", "WARNING", "ERROR"] | None = None,
                  date_from: AwareDatetime | None = None, date_to: AwareDatetime | None = None,
                  limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(422, "date_from debe ser menor o igual a date_to")
        try:
            return store.list(camera=camera, event_type=event_type, level=level, date_from=date_from,
                              date_to=date_to, limit=limit, offset=offset)
        except Exception:
            logger.exception("Error consultando logs")
            raise HTTPException(503, "Almacenamiento de logs no disponible") from None

    @router.get("/{log_id}", response_model=InferenceLog)
    def get_log(log_id: str):
        try:
            record = store.get(log_id)
        except Exception:
            logger.exception("Error consultando log")
            raise HTTPException(503, "Almacenamiento de logs no disponible") from None
        if record is None:
            raise HTTPException(404, "Log no encontrado")
        return record

    return router
