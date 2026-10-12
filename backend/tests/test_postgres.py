"""Integración optativa contra PostgreSQL real, aislada en un esquema temporal."""
import os
from uuid import uuid4
from urllib.parse import quote
import pytest


def test_postgres_stores_and_http(monkeypatch):
    url = os.getenv("SENTRIX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requiere SENTRIX_TEST_DATABASE_URL")
    import psycopg
    from psycopg import sql
    from fastapi.testclient import TestClient
    from app.detections import DetectionStore
    from app.alerts import AlertStore, EventPolicy
    from app.inference_logs import InferenceLogStore
    from app.main import create_app
    schema = "test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        isolated = url + ("&" if "?" in url else "?") + "options=" + quote("-csearch_path=" + schema)
        try:
            detection = DetectionStore(isolated)
            alerts = AlertStore(isolated)
            logs = InferenceLogStore(isolated)
            prediction = dict(clase="Hurto", confianza=.75, timestamp=1.25, simulated=False)
            record = detection.save(prediction, "camera", session_id="session")
            assert DetectionStore(isolated).get(record["id"]) == record
            policy = EventPolicy(confirmation=1)
            event = alerts.apply(policy.update(prediction, 1.25), record)
            assert event["status"] == "active"
            assert alerts.list()["total"] == 1
            assert alerts.interrupt("session", "source_disconnected") == 1
            assert alerts.get(event["id"])["status"] == "interrupted"
            assert alerts.apply(policy.update(prediction, 1.25), record) is None
            log = logs.write("inference_completed", "prueba", detection_id=record["id"])
            assert logs.get(log["id"])["message"] == "prueba"
            assert logs.list()["total"] == 1
            monkeypatch.setenv("SENTRIX_DATABASE_URL", isolated)
            with TestClient(create_app(mode="mock", transport="webrtc")) as client:
                assert "/auth/verify" not in client.get("/openapi.json").json()["paths"]
                with psycopg.connect(isolated) as check:
                    assert check.execute("SELECT to_regclass('users')").fetchone()[0] is None
                result = client.post("/predict", json={"timestamp": 0, "frames": [[[[0, 0, 0]]]]})
                assert result.status_code == 200
                assert client.get("/detections/" + result.headers["X-Detection-ID"]).status_code == 200
                assert client.get("/inference-logs").status_code == 200
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
