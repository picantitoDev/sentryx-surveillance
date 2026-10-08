import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from aiortc import RTCPeerConnection, RTCConfiguration
from app.main import create_app, Prediction
from app.detections import DetectionStore
from app.webrtc import WebRTCReceiver, Session
from app.pipeline import VideoMAEBuffer, VideoMAEConfig
from app.videomae import SessionAlertPolicy, load_metadata, inference_helpers, MODEL_ROOT


def test_http_persistence_restart_and_queries(tmp_path):
    class LowConfidence:
        def predict(self, window):
            return Prediction(clase="Hurto", confianza=.2, timestamp=window.timestamp, simulated=True)
    path = tmp_path / "data" / "detections.sqlite3"
    with TestClient(create_app(LowConfidence(), transport="webrtc", detection_db=path)) as client:
        response = client.post("/predict", json={"source_id": "camera_2", "timestamp": 12.5,
                                                "frames": [[[[0, 0, 0]]]]})
        assert response.status_code == 200
        identifier = response.headers["X-Detection-ID"]
    with TestClient(create_app(mode="mock", transport="webrtc", detection_db=path)) as client:
        record = client.get(f"/detections/{identifier}").json()
        assert record["id"] == identifier and record["camera"] == "camera_2"
        assert record["timestamp"] == 12.5 and record["confianza"] == .2
        assert record["clase"] == "Hurto" and record["simulated"] is True
        assert datetime.fromisoformat(record["fecha_hora"]).utcoffset().total_seconds() == 0
        assert client.get("/detections?camera=camera_2&limit=1").json() == [record]
        assert client.get("/detections?camera=other").json() == []
        assert client.get("/detections?offset=1").json() == []
        assert client.get("/detections?limit=0").status_code == 422
        assert client.get("/detections/nonexistent").status_code == 404
    assert [p.name for p in path.parent.iterdir()] == ["detections.sqlite3"]


def test_concurrent_writes(tmp_path):
    store = DetectionStore(tmp_path / "detections.sqlite3")
    prediction = dict(clase="Normal", confianza=.9, timestamp=0, simulated=False)
    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(lambda _: store.save(prediction, "camera"), range(30)))
    assert len({r["id"] for r in records}) == len(store.list()) == 30


def test_stream_persists_before_alert_threshold_and_survives_close(tmp_path):
    async def run():
        store = DetectionStore(tmp_path / "detections.sqlite3")
        probabilities = dict(normal=.8, hurto=.05, robo=.05, agresion_fisica=.05, vandalismo=.05)
        def predict(frames, timestamp):
            return dict(clase="Normal", confianza=.8, timestamp=timestamp,
                        simulated=False, probabilities=probabilities)
        config = VideoMAEConfig()
        receiver = WebRTCReceiver(predict, config, predict_frames=predict, detection_store=store)
        session = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])), source_id="cam_live",
                          buffer=VideoMAEBuffer(config), alert_policy=SessionAlertPolicy(
                              load_metadata(), inference_helpers(MODEL_ROOT)))
        receiver.sessions[session.id] = session
        receiver.spawn(session, receiver.process_windows(session))
        try:
            session.buffer.queue.put_nowait((1, [(None, i / 30, 0) for i in range(16)]))
            async with asyncio.timeout(5):
                while session.windows_processed != 1:
                    await asyncio.sleep(.01)
            assert session.latest_result["alert"]["active"] is False
            assert session.latest_result["alert"]["candidate"] is False
            identifier = session.latest_result["detection_id"]
        finally:
            await receiver.shutdown()
        record = store.get(identifier)
        assert record["session_id"] == session.id
        assert record["window_id"] == f"{session.id}:1"
        assert record["camera"] == "cam_live" and record["probabilities"] == probabilities
        assert record["clase"] == "Normal" and record["timestamp"] == .5
    asyncio.run(run())


def test_storage_failure_is_not_reported_as_success(tmp_path):
    path = tmp_path / "blocked"
    path.write_text("not a directory")
    with TestClient(create_app(mode="mock", transport="webrtc", detection_db=path / "db")) as client:
        response = client.post("/predict", json={"timestamp": 0, "frames": [[[[0, 0, 0]]]]})
        assert response.status_code == 503
        assert "X-Detection-ID" not in response.headers
        assert client.get("/detections").status_code == 503
