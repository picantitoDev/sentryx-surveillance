import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from aiortc import RTCPeerConnection, RTCConfiguration
from app.alerts import AlertStore, EventPolicy, DEFAULT_THRESHOLDS
from app.detections import DetectionStore
from app.main import create_app
from app.pipeline import PipelineConfig, WindowBuffer
from app.webrtc import Session, WebRTCReceiver
from test_pipeline import rgb_frame


def result(clase="Hurto", confidence=.9, timestamp=1):
    return dict(clase=clase, confianza=confidence, timestamp=timestamp, simulated=True)


@pytest.mark.parametrize("clase,threshold", DEFAULT_THRESHOLDS.items())
def test_class_threshold_confirmation_and_normal_release(clase, threshold):
    policy = EventPolicy()
    assert not policy.update(result(clase, threshold-.01), 0)["alert"]["candidate"]
    assert not policy.update(result(clase, threshold), 1)["alert"]["started"]
    assert policy.update(result(clase, threshold), 2)["alert"]["started"]
    # Confianza baja y cambio criminal no cierran el intervalo ni cambian su clase inicial.
    for value in (result(clase, .1), result("Vandalismo", .95)):
        state = policy.update(value, 3)["alert"]
        assert state["active"] and state["clase"] == clase
    assert not policy.update(result("Normal"), 4)["alert"]["ended"]
    assert not policy.update(result("Normal"), 5)["alert"]["ended"]
    assert policy.update(result("Normal"), 6)["alert"]["ended"]


def test_candidate_class_and_normal_confirmation_must_be_consecutive():
    policy = EventPolicy()
    assert not policy.update(result("Hurto"), 1)["alert"]["started"]
    assert not policy.update(result("Robo"), 2)["alert"]["started"]
    assert policy.update(result("Robo"), 3)["alert"]["started"]
    policy.update(result("Normal"), 4)
    policy.update(result("Normal"), 5)
    policy.update(result("Hurto", .1), 6)
    assert not policy.update(result("Normal"), 7)["alert"]["ended"]


def test_store_interval_references_idempotence_and_concurrent_open(tmp_path):
    detections = DetectionStore(tmp_path / "db")
    alerts = AlertStore(detections.path)
    opening = detections.save(result(timestamp=12.5), "cam", session_id="s", window_id="s:2")
    state = dict(alert=dict(started=True, ended=False, threshold=.5))
    with ThreadPoolExecutor(max_workers=4) as executor:
        events = list(executor.map(lambda _: alerts.apply(state, opening), range(8)))
    assert len({e["id"] for e in events}) == alerts.list()["total"] == 1
    event = events[0]
    assert event["status"] == "active" and event["timestamp_fin"] is None
    closing = detections.save(result("Normal", .9, 24.8), "cam", session_id="s", window_id="s:9")
    closed = alerts.apply(dict(alert=dict(started=False, ended=True)), closing)
    assert closed["id"] == event["id"] and closed["status"] == "closed"
    assert closed["timestamp_inicio"] == 12.5 and closed["timestamp_fin"] == 24.8
    assert closed["duracion_segundos"] == pytest.approx(12.3)
    assert closed["detection_id"] == opening["id"] and closed["end_detection_id"] == closing["id"]
    assert alerts.get(event["id"]) == closed
    assert alerts.apply(dict(alert=dict(started=False, ended=True)), closing) is None
    assert alerts.list(camera="other")["total"] == 0


def test_interruption_never_invents_normal_or_duration(tmp_path):
    store = AlertStore(tmp_path / "db")
    detection = DetectionStore(store.path).save(result(), "cam", session_id="s")
    event = store.apply(dict(alert=dict(started=True, ended=False, threshold=.5)), detection)
    store.interrupt("s", "source_disconnected")
    interrupted = store.get(event["id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["interruption_reason"] == "source_disconnected"
    assert interrupted["timestamp_fin"] is interrupted["duracion_segundos"] is None
    # Un hilo de inferencia que termina después del cierre no reabre la sesión.
    assert store.apply(dict(alert=dict(started=True, ended=False, threshold=.5)), detection) is None


def test_stream_events_persist_and_two_sessions_are_isolated(tmp_path):
    async def run():
        detections = DetectionStore(tmp_path / "db")
        alerts = AlertStore(detections.path)
        values = iter([result(timestamp=0), result(timestamp=.25), result(timestamp=.5),
                       result("Normal", .9, .75), result("Normal", .9, 1), result("Normal", .9, 1.25)])
        def predict(frames, timestamp):
            return next(values)
        config = PipelineConfig(window_size=1, width=2, height=2)
        receiver = WebRTCReceiver(predict, config, detection_store=detections, alert_store=alerts)
        session = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])), source_id="cam",
                          buffer=WindowBuffer(config), alert_policy=EventPolicy())
        receiver.sessions[session.id] = session
        receiver.spawn(session, receiver.process_windows(session))
        try:
            for i in range(6):
                session.buffer.push(rgb_frame(), i*.25)
                await asyncio.wait_for(session.buffer.queue.join(), 5)
                if i == 1:
                    assert session.latest_result["alert"]["id"] is not None
                    assert alerts.list(status="active")["total"] == 1
            assert session.latest_result["alert"]["status"] == "closed"
        finally:
            await receiver.shutdown()
        event = alerts.list()["items"][0]
        assert event["timestamp_inicio"] == .25 and event["timestamp_fin"] == 1.25
        assert event["duracion_segundos"] == 1
        assert len(detections.list()) == 6 and alerts.list()["total"] == 1
        other = detections.save(result(timestamp=0), "cam_b", session_id="other")
        alerts.apply(dict(alert=dict(started=True, ended=False, threshold=.5)), other)
        assert alerts.list(camera="cam_b", status="active")["total"] == 1
        assert alerts.get(event["id"])["status"] == "closed"
    asyncio.run(run())


def test_api_recovery_after_restart_and_http_is_not_temporal(tmp_path):
    path = tmp_path / "db"
    with TestClient(create_app(mode="mock", transport="webrtc", detection_db=path)) as client:
        app_store = AlertStore(path)
        detection = DetectionStore(path).save(result(), "cam", session_id="s")
        event = app_store.apply(dict(alert=dict(started=True, ended=False, threshold=.5)), detection)
        assert client.get("/alerts?camera=cam&status=active").json()["total"] == 1
        assert client.get(f'/alerts/{event["id"]}').json() == event
        assert client.get("/alerts/none").status_code == 404
        assert client.get("/alerts?limit=0").status_code == 422
        assert client.get("/alerts?status=invalid").status_code == 422
        assert client.post("/predict", json={"timestamp": 0, "frames": [[[[0, 0, 0]]]]}).status_code == 200
        assert app_store.list()["total"] == 1
    with TestClient(create_app(mode="mock", transport="webrtc", detection_db=path)) as client:
        recovered = client.get(f'/alerts/{event["id"]}').json()
        assert recovered["status"] == "interrupted" and recovered["interruption_reason"] == "service_restarted"
        assert recovered["timestamp_fin"] is None


def test_invalid_configuration_and_invalid_open(tmp_path):
    with pytest.raises(ValueError):
        EventPolicy({"Hurto": .5})
    with pytest.raises(ValueError):
        EventPolicy({**DEFAULT_THRESHOLDS, "Hurto": float("nan")})
    store = AlertStore(tmp_path / "db")
    detection = DetectionStore(store.path).save(result(confidence=.1), "cam", session_id="s")
    with pytest.raises(ValueError):
        store.apply(dict(alert=dict(started=True, ended=False, threshold=.5)), detection)


def test_failed_window_interrupts_active_event(tmp_path):
    async def run():
        detections = DetectionStore(tmp_path / "db")
        alerts = AlertStore(detections.path)
        calls = 0
        def predict(frames, timestamp):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("model failure")
            return result(timestamp=timestamp)
        config = PipelineConfig(window_size=1, width=2, height=2)
        receiver = WebRTCReceiver(predict, config, detection_store=detections, alert_store=alerts)
        session = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])), source_id="cam",
                          buffer=WindowBuffer(config), alert_policy=EventPolicy())
        receiver.sessions[session.id] = session
        receiver.spawn(session, receiver.process_windows(session))
        try:
            for i in range(3):
                session.buffer.push(rgb_frame(), i*.25)
                await asyncio.wait_for(session.buffer.queue.join(), 5)
            event = alerts.list()["items"][0]
            assert event["status"] == "interrupted" and event["interruption_reason"] == "processing_error"
            assert event["timestamp_fin"] is None and session.processing_errors == 1
        finally:
            await receiver.shutdown()
    asyncio.run(run())
