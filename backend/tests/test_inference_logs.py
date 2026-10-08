import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from aiortc import RTCPeerConnection, RTCConfiguration
from app.main import create_app
from app.inference_logs import InferenceLogStore
from app.pipeline import PipelineConfig, WindowBuffer
from app.webrtc import Session, WebRTCReceiver
from test_pipeline import rgb_frame


PAYLOAD = {"source_id": "cam_test", "timestamp": 1.5, "frames": [[[[0, 0, 0]]]]}


def test_http_logs_lifecycle_timing_and_restart(tmp_path):
    path = tmp_path / "logs.sqlite3"
    with TestClient(create_app(mode="mock", transport="webrtc", log_db=path)) as client:
        response = client.post("/predict", json=PAYLOAD)
        page = client.get("/inference-logs", params={"camera": "cam_test"}).json()
        assert page["total"] == 1
        record = page["items"][0]
        assert record["event_type"] == "inference_completed"
        assert record["detection_id"] == response.headers["X-Detection-ID"]
        assert record["processing_ms"] >= record["inference_ms"] >= 0
        assert client.get(f'/inference-logs/{record["id"]}').json() == record
        assert client.get("/inference-logs/missing").status_code == 404
    store = InferenceLogStore(path)
    assert store.get(record["id"]) is not None
    assert {r["event_type"] for r in store.list()["items"]} == {
        "service_started", "service_stopped", "inference_completed"}


def test_filters_dates_and_pagination(tmp_path):
    app = create_app(mode="mock", transport="webrtc", log_db=tmp_path / "logs")
    with TestClient(app) as client:
        before = datetime.now(timezone.utc).isoformat()
        first = app.state.log_store.write("source_error", "Error controlado", camera="a", level="ERROR")
        app.state.log_store.write("source_connected", "Conectada", camera="b")
        after = datetime.now(timezone.utc).isoformat()
        page = client.get("/inference-logs", params={"date_from": before, "date_to": after}).json()
        assert page["total"] == 2
        filtered = client.get("/inference-logs", params={"camera": "a", "level": "ERROR",
                              "event_type": "source_error"}).json()
        assert filtered["total"] == 1 and filtered["items"][0]["id"] == first["id"]
        assert client.get("/inference-logs?limit=1&offset=1").json()["total"] == 3
        assert len(client.get("/inference-logs?limit=1&offset=1").json()["items"]) == 1
        assert client.get("/inference-logs?camera=missing").json()["items"] == []
        instant = first["fecha_hora"]
        assert client.get("/inference-logs", params={"date_from": instant, "date_to": instant}).json()["total"] == 1
        for params in ({"limit": 0}, {"offset": -1}, {"event_type": "invalid"}, {"level": "DEBUG"},
                       {"date_from": "2026-10-06T10:00:00"}, {"date_from": after, "date_to": before}):
            assert client.get("/inference-logs", params=params).status_code == 422


def test_errors_do_not_expose_exception_secrets(tmp_path):
    class Failing:
        def predict(self, window):
            raise RuntimeError("secret-token")
    with TestClient(create_app(Failing(), transport="webrtc")) as client:
        assert client.post("/predict", json=PAYLOAD).status_code == 503
        page = client.get("/inference-logs?event_type=inference_error").json()
        assert page["total"] == 1 and page["items"][0]["level"] == "ERROR"
        assert "secret-token" not in str(page)


def test_log_storage_failure_does_not_break_prediction(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    with TestClient(create_app(mode="mock", transport="webrtc", log_db=blocked / "logs")) as client:
        assert client.post("/predict", json=PAYLOAD).status_code == 200
        assert client.get("/inference-logs").status_code == 503
        assert client.get("/inference-logs/any").status_code == 503


def test_detection_storage_failure_has_separate_log(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    with TestClient(create_app(mode="mock", transport="webrtc", detection_db=blocked / "db")) as client:
        assert client.post("/predict", json=PAYLOAD).status_code == 503
        record = client.get("/inference-logs?event_type=detection_storage_error").json()["items"][0]
        assert record["level"] == "ERROR" and record["camera"] == "cam_test"
        assert record["inference_ms"] >= 0 and record["detection_id"] is None


def test_livekit_transport_and_source_logs(tmp_path):
    from app.livekit_receiver import LiveKitReceiver
    from test_livekit import Room, Publication, Stream, settings, participant, frame_event, wait_for
    async def run():
        store = InferenceLogStore(tmp_path / "logs")
        room = Room([participant("gabriel", [Publication()])])
        stream = Stream()
        def predict(frames, timestamp):
            return dict(clase="Normal", confianza=.9, timestamp=timestamp, simulated=True)
        receiver = LiveKitReceiver(predict, PipelineConfig(width=2, height=2, window_size=1),
            settings=settings(), room_factory=lambda: room, stream_factory=lambda track: stream, log_store=store)
        try:
            await receiver.start()
            await wait_for(lambda: bool(receiver.sessions))
            session = next(iter(receiver.sessions.values()))
            stream.queue.put_nowait(frame_event())
            await wait_for(lambda: session.windows_processed == 1)
            assert store.list(camera=settings().source_id, event_type="source_connected")["total"] == 1
            room.emit("disconnected")
            await wait_for(lambda: not receiver.sessions)
        finally:
            await receiver.shutdown()
        events = {r["event_type"] for r in store.list()["items"]}
        assert {"transport_connected", "transport_disconnected", "source_connected",
                "source_disconnected", "inference_completed"} <= events
    asyncio.run(run())


def test_concurrent_log_writes(tmp_path):
    store = InferenceLogStore(tmp_path / "logs")
    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(lambda _: store.write("inference_completed", "Completada"), range(24)))
    assert len({r["id"] for r in records}) == store.list()["total"] == 24


def test_stream_success_and_error_have_camera_window_and_time(tmp_path):
    async def run():
        store = InferenceLogStore(tmp_path / "logs")
        calls = 0
        def predict(frames, timestamp):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("internal")
            return dict(clase="Normal", confianza=.9, timestamp=timestamp, simulated=True)
        config = PipelineConfig(width=2, height=2, window_size=1)
        receiver = WebRTCReceiver(predict, config, log_store=store)
        session = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])),
                          source_id="stream_cam", buffer=WindowBuffer(config))
        receiver.sessions[session.id] = session
        receiver.spawn(session, receiver.process_windows(session))
        try:
            session.buffer.push(rgb_frame(), 0)
            await asyncio.wait_for(session.buffer.queue.join(), timeout=5)
            session.buffer.push(rgb_frame(), .25)
            await asyncio.wait_for(session.buffer.queue.join(), timeout=5)
        finally:
            await receiver.shutdown()
        records = store.list(camera="stream_cam")["items"]
        assert {r["event_type"] for r in records} == {
            "inference_error", "inference_completed", "source_disconnected"}
        for record in records:
            assert record["session_id"] == session.id
            if record["event_type"].startswith("inference_"):
                assert record["window_id"].startswith(session.id + ":")
                assert record["processing_ms"] >= 0
    asyncio.run(run())
