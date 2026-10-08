"""EN-4: eventos confirmados con intervalo de video y trazabilidad EN-3."""
from datetime import datetime, timezone
from uuid import uuid4
import json
import os

from fastapi import APIRouter, HTTPException, Query
from typing import Literal
from app.detections import DetectionStore

DEFAULT_THRESHOLDS = {"Hurto": .50, "Robo": .60, "Agresión física": .60, "Vandalismo": .55}


class EventPolicy:
    def __init__(self, thresholds=None, confirmation=2, normal_confirmation=3):
        self.thresholds = dict(thresholds or DEFAULT_THRESHOLDS)
        if set(self.thresholds) != set(DEFAULT_THRESHOLDS) or any(
                not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in self.thresholds.values()):
            raise ValueError("Se requieren cuatro thresholds finitos entre 0 y 1")
        if confirmation < 1 or normal_confirmation < 1:
            raise ValueError("Las confirmaciones deben ser positivas")
        self.confirmation = confirmation
        self.normal_confirmation = normal_confirmation
        self.reset()

    @classmethod
    def from_env(cls):
        value = os.getenv("SENTRIX_ALERT_THRESHOLDS")
        return cls(json.loads(value) if value else None)

    def reset(self):
        self.active_class = None
        self.candidate_class = None
        self.candidate_count = 0
        self.release_count = 0
        self.start_timestamp = None

    def update(self, result, timestamp):
        clase, confidence = result["clase"], result["confianza"]
        candidate = clase in self.thresholds and confidence >= self.thresholds[clase]
        started = ended = False
        if self.active_class is None:
            self.candidate_count = self.candidate_count + 1 if candidate and clase == self.candidate_class else int(candidate)
            self.candidate_class = clase if candidate else None
            if self.candidate_count >= self.confirmation:
                self.active_class = clase
                self.start_timestamp = timestamp
                started = True
        else:
            # Una confianza criminal baja no equivale a haber vuelto a Normal.
            self.release_count = self.release_count + 1 if clase == "Normal" else 0
            if self.release_count >= self.normal_confirmation:
                ended = True
                self.active_class = None
                self.start_timestamp = None
                self.candidate_class = None
                self.candidate_count = 0
                self.release_count = 0
        return {**result, "alert": dict(active=self.active_class is not None, candidate=candidate,
            started=started, ended=ended, clase=self.active_class, start_timestamp=self.start_timestamp,
            candidate_count=self.candidate_count, release_count=self.release_count,
            threshold=self.thresholds.get(self.active_class or clase))}


class AlertStore(DetectionStore):
    def connect(self):
        connection = super().connect()
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY, detection_id TEXT NOT NULL REFERENCES detections(id),
            end_detection_id TEXT REFERENCES detections(id), camera TEXT NOT NULL,
            session_id TEXT NOT NULL, clase TEXT NOT NULL, confianza REAL NOT NULL,
            threshold REAL NOT NULL, timestamp REAL NOT NULL, fecha_hora TEXT NOT NULL,
            timestamp_inicio REAL NOT NULL, timestamp_fin REAL, fecha_hora_fin TEXT,
            duracion_segundos REAL, status TEXT NOT NULL, interruption_reason TEXT,
            UNIQUE(detection_id)
        )""")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS active_session ON alerts(session_id) WHERE status = 'active'")
        connection.execute("CREATE TABLE IF NOT EXISTS closed_alert_sessions (session_id TEXT PRIMARY KEY)")
        return connection

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def apply(self, result, detection):
        state = result["alert"]
        connection = self.connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT 1 FROM closed_alert_sessions WHERE session_id=?",
                                      (detection["session_id"],)).fetchone():
                    return None
                active = connection.execute("SELECT * FROM alerts WHERE session_id = ? AND status = 'active'",
                                            (detection["session_id"],)).fetchone()
                if state["started"] and active is None:
                    if detection["clase"] not in DEFAULT_THRESHOLDS or detection["confianza"] < state["threshold"]:
                        raise ValueError("La detección no cumple el threshold")
                    identifier = str(uuid4())
                    connection.execute("""INSERT OR IGNORE INTO alerts
                        (id, detection_id, camera, session_id, clase, confianza, threshold, timestamp,
                         fecha_hora, timestamp_inicio, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                        (identifier, detection["id"], detection["camera"], detection["session_id"],
                         detection["clase"], detection["confianza"], state["threshold"], detection["timestamp"],
                         detection["fecha_hora"], detection["timestamp"]))
                    active = connection.execute("SELECT * FROM alerts WHERE detection_id = ?", (detection["id"],)).fetchone()
                if state["ended"] and active is not None:
                    if detection["clase"] != "Normal" or detection["timestamp"] < active["timestamp_inicio"]:
                        raise ValueError("Cierre de evento inválido")
                    connection.execute("""UPDATE alerts SET status='closed', end_detection_id=?, timestamp_fin=?,
                        fecha_hora_fin=?, duracion_segundos=? WHERE id=?""",
                        (detection["id"], detection["timestamp"], detection["fecha_hora"],
                         detection["timestamp"]-active["timestamp_inicio"], active["id"]))
                    active = connection.execute("SELECT * FROM alerts WHERE id=?", (active["id"],)).fetchone()
                return dict(active) if active is not None else None
        finally:
            connection.close()

    def interrupt(self, session_id=None, reason="service_restarted"):
        connection = self.connect()
        try:
            with connection:
                if session_id is not None and reason == "source_disconnected":
                    connection.execute("INSERT OR IGNORE INTO closed_alert_sessions VALUES (?)", (session_id,))
                cursor = connection.execute("UPDATE alerts SET status='interrupted', interruption_reason=?, fecha_hora_fin=?"
                    " WHERE status='active'" + (" AND session_id=?" if session_id is not None else ""),
                    (reason, self.now(), session_id) if session_id is not None else (reason, self.now()))
                return cursor.rowcount
        finally:
            connection.close()

    def get(self, identifier):
        connection = self.connect()
        try:
            row = connection.execute("SELECT * FROM alerts WHERE id=?", (identifier,)).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def list(self, camera=None, status=None, limit=100, offset=0):
        connection = self.connect()
        clauses, params = [], []
        for column, value in (("camera", camera), ("status", status)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            with connection:
                connection.execute("BEGIN")
                total = connection.execute("SELECT COUNT(*) FROM alerts" + where, params).fetchone()[0]
                rows = connection.execute("SELECT * FROM alerts" + where + " ORDER BY fecha_hora DESC, id DESC LIMIT ? OFFSET ?",
                                          (*params, limit, offset)).fetchall()
            return dict(items=[dict(row) for row in rows], total=total, limit=limit, offset=offset)
        finally:
            connection.close()


def alerts_router(store):
    router = APIRouter(prefix="/alerts", tags=["Alertas EN-4"])

    @router.get("")
    def list_alerts(camera: str | None = None, status: Literal["active", "closed", "interrupted"] | None = None,
                    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        try:
            return store.list(camera, status, limit, offset)
        except Exception:
            raise HTTPException(503, "Almacenamiento de alertas no disponible") from None

    @router.get("/{alert_id}")
    def get_alert(alert_id: str):
        try:
            result = store.get(alert_id)
        except Exception:
            raise HTTPException(503, "Almacenamiento de alertas no disponible") from None
        if result is None:
            raise HTTPException(404, "Alerta no encontrada")
        return result

    return router
