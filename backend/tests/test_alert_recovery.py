"""Regressions for schema locks and RTSP recovery; no camera connection."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.infrastructure.persistence.alerts import AlertStore
from app.domain.alerts import EventPolicy
from app.infrastructure.persistence.detections import DetectionStore


def test_concurrent_alert_open_and_disconnect(tmp_path):
    detections = DetectionStore(tmp_path / "events.db")
    alerts = AlertStore(detections.path)
    record = detections.save(dict(clase="Hurto", confianza=.9, timestamp=1,
                                 simulated=True), "cam", session_id="session")
    opening = dict(alert=dict(started=True, ended=False, threshold=.5))
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(alerts.apply, opening, record) for _ in range(6)]
        futures.append(executor.submit(alerts.interrupt, "session", "source_disconnected"))
        for future in futures:
            future.result(timeout=10)
    assert alerts.list(status="active")["total"] == 0
    assert alerts.apply(opening, record) is None
    for event in alerts.list()["items"]:
        assert event["status"] == "interrupted"
        assert event["timestamp_fin"] is None


def test_schema_commits_before_writes_and_is_not_repeated(monkeypatch):
    import app.infrastructure.persistence.detections as module
    operations = []

    class Connection:
        def execute(self, statement, parameters=None):
            operations.append(statement)
            return SimpleNamespace(rowcount=0)

        def __enter__(self):
            return self

        def __exit__(self, kind, value, traceback):
            operations.append("ROLLBACK" if kind else "COMMIT")

        def close(self):
            operations.append("CLOSE")

    monkeypatch.setattr(module, "connect", lambda _: Connection())
    store = AlertStore("postgresql://unused")
    store.interrupt("s")
    update = next(i for i, value in enumerate(operations) if value.startswith("UPDATE"))
    assert operations.index("COMMIT") < operations.index("BEGIN IMMEDIATE") < update
    assert operations.index("SELECT pg_advisory_xact_lock(734821906)") < next(
        i for i, value in enumerate(operations) if value.startswith("CREATE"))
    operations.clear()
    store.interrupt("s")
    assert not any(value.startswith("CREATE") for value in operations)


@pytest.mark.parametrize("sqlstate,expected_attempts", [("40P01", 3), ("40001", 3), ("23503", 1)])
def test_transaction_retry_is_bounded_and_rolls_back(monkeypatch, sqlstate, expected_attempts):
    import app.infrastructure.persistence.alerts as module
    attempts, rollbacks, closed = [], [], []

    class Failure(Exception):
        pass

    error = Failure("database conflict")
    error.sqlstate = sqlstate

    class Connection:
        def execute(self, statement, parameters=None):
            if statement.startswith("UPDATE"):
                attempts.append(1)
                raise error

        def __enter__(self):
            return self

        def __exit__(self, kind, value, traceback):
            rollbacks.append(kind is Failure)

        def close(self):
            closed.append(1)

    store = AlertStore("unused.db")
    monkeypatch.setattr(store, "connect", Connection)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with pytest.raises(Failure):
        store.interrupt("s")
    assert len(attempts) == len(closed) == expected_attempts
    assert rollbacks == [True] * expected_attempts


def test_rtsp_reconnects_when_alerts_logs_and_close_fail(configured, monkeypatch):
    import app.infrastructure.streaming.rtsp as module
    from app.domain.pipeline import PipelineConfig, WindowBuffer
    from app.infrastructure.streaming.webrtc import Session

    class BrokenStore:
        def interrupt(self, *args):
            raise RuntimeError("storage unavailable")

        def emit(self, *args, **kwargs):
            raise RuntimeError("logs unavailable")

    async def exercise():
        receiver = module.RTSPReceiver(lambda *args: {}, detection_store=configured, alert_store=BrokenStore(),
                                       log_store=BrokenStore())
        session = Session(pc=module._RTSPPeer(), source_id="cam",
                          buffer=WindowBuffer(PipelineConfig()), alert_policy=EventPolicy())
        session.camera_config = receiver.default_camera
        receiver.sessions[session.id] = session
        opened = []

        class Container:
            def decode(self, **kwargs):
                return iter(())

            def close(self):
                raise RuntimeError("close failed")

        def open_stream(*args, **kwargs):
            opened.append(1)
            return Container()

        async def retry_sleep(delay):
            assert delay == 3
            if len(opened) == 2:
                receiver.sessions.pop(session.id)

        monkeypatch.setattr(module.av, "open", open_stream)
        monkeypatch.setattr(module.asyncio, "sleep", retry_sleep)
        await asyncio.wait_for(receiver._read_loop(session), 5)
        assert len(opened) == 2
        assert session.pending_alert_interruption
        assert session.rtsp_status == "reconnecting"

    asyncio.run(exercise())


def test_postgres_concurrent_schema_and_alert_writes():
    import os
    from urllib.parse import quote
    from uuid import uuid4

    url = os.getenv("SENTRIX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requiere SENTRIX_TEST_DATABASE_URL")
    # Neon transaction poolers reject search_path in startup options.
    # Use the direct endpoint so every connection stays in the test schema.
    url = url.replace("-pooler.", ".", 1)
    import psycopg
    from psycopg import sql

    schema = "test_alert_recovery_" + uuid4().hex
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        isolated = url + ("&" if "?" in url else "?") + "options=" + quote(
            "-csearch_path=" + schema + " -clock_timeout=30000 -cstatement_timeout=60000")
        try:
            records = [DetectionStore(isolated).save(
                dict(clase="Hurto", confianza=.9, timestamp=1, simulated=True),
                "cam", session_id=f"s{i}") for i in range(6)]
            opening = dict(alert=dict(started=True, ended=False, threshold=.5))

            def open_and_interrupt(record):
                # Independent stores force concurrent first-time schema initialization.
                store = AlertStore(isolated)
                event = store.apply(opening, record)
                assert event["status"] == "active"
                assert store.interrupt(record["session_id"], "source_disconnected") == 1
                assert store.apply(opening, record) is None
                assert store.get(event["id"])["status"] == "interrupted"

            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(open_and_interrupt, records))
            store = AlertStore(isolated)
            assert store.list(status="active")["total"] == 0
            assert store.list(status="interrupted")["total"] == 6
            from app.infrastructure.persistence.inference_logs import InferenceLogStore

            def write_logs(worker):
                logs = InferenceLogStore(isolated)
                return [logs.write("inference_completed", f"window {i}",
                                   camera=f"cam{worker}", window_id=f"{worker}:{i}")
                        for i in range(4)]

            with ThreadPoolExecutor(max_workers=6) as executor:
                batches = list(executor.map(write_logs, range(6)))
            logs = InferenceLogStore(isolated)
            assert logs.list()["total"] == 24
            assert logs.list(camera="cam0")["total"] == 4
            for batch in batches:
                for record in batch:
                    assert logs.get(record["id"]) == record
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
