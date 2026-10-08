"""Persistencia de metadatos de inferencia; nunca almacena frames ni clips."""
import json
from app.database import connect
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class DetectionStore:
    def __init__(self, path):
        self.path = str(path) if str(path).startswith(("postgresql://", "postgres://")) else Path(path)

    def connect(self):
        connection = connect(self.path)
        connection.execute("""CREATE TABLE IF NOT EXISTS detections (
            id TEXT PRIMARY KEY, camera TEXT NOT NULL, fecha_hora TEXT NOT NULL,
            clase TEXT NOT NULL, confianza REAL NOT NULL CHECK(confianza BETWEEN 0 AND 1),
            timestamp REAL NOT NULL CHECK(timestamp >= 0), simulated INTEGER NOT NULL,
            session_id TEXT, window_id TEXT, probabilities TEXT
        )""")
        return connection

    @staticmethod
    def decode(row):
        result = dict(row)
        result["simulated"] = bool(result["simulated"])
        result["probabilities"] = json.loads(result["probabilities"]) if result["probabilities"] else None
        return result

    def save(self, prediction, camera, *, session_id=None, window_id=None):
        record = dict(id=str(uuid4()), camera=camera,
                      fecha_hora=datetime.now(timezone.utc).isoformat(),
                      clase=prediction["clase"], confianza=prediction["confianza"],
                      timestamp=prediction["timestamp"], simulated=prediction["simulated"],
                      session_id=session_id, window_id=window_id,
                      probabilities=prediction.get("probabilities"))
        connection = self.connect()
        try:
            with connection:
                connection.execute("INSERT INTO detections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*[record[key] for key in ("id", "camera", "fecha_hora", "clase", "confianza",
                        "timestamp", "simulated", "session_id", "window_id")],
                     json.dumps(record["probabilities"]) if record["probabilities"] is not None else None))
        finally:
            connection.close()
        return record

    def get(self, detection_id):
        connection = self.connect()
        try:
            row = connection.execute("SELECT * FROM detections WHERE id = ?", (detection_id,)).fetchone()
            return self.decode(row) if row else None
        finally:
            connection.close()

    def list(self, camera=None, limit=100, offset=0):
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM detections " + ("WHERE camera = ? " if camera is not None else "") +
                "ORDER BY fecha_hora DESC, id DESC LIMIT ? OFFSET ?",
                ((camera,) if camera is not None else ()) + (limit, offset)).fetchall()
            return [self.decode(row) for row in rows]
        finally:
            connection.close()
