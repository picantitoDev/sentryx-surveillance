import asyncio
from pathlib import Path
from fractions import Fraction
import os
import time
import threading

import cv2
import httpx
import numpy as np
import pytest
from av import VideoFrame
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription, VideoStreamTrack
from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import VideoMAEBuffer, VideoMAEConfig
from app.webrtc import WebRTCReceiver, Session
from app.videomae import (VideoMAEPredictor, MODEL_ROOT, CLASS_NAMES,
                         load_metadata, inference_helpers, SessionAlertPolicy)


def test_frame_sampling_matches_piero_and_keeps_recent_windows():
    buffer = VideoMAEBuffer(VideoMAEConfig(queue_size=1))
    for index in range(125):
        buffer.push(index, index / 25)
    number, samples = buffer.queue.get_nowait()
    assert number == 2 and buffer.dropped == 1
    assert [sample[0] for sample in samples] == list(range(64, 125, 4))
    assert buffer.sampled == 32 and not buffer.partial


def test_frame_sampling_resets_on_invalid_or_discontinuous_time():
    buffer = VideoMAEBuffer(VideoMAEConfig())
    for index in range(10):
        buffer.push("old", index / 25)
    buffer.push("invalid", float("nan"))
    assert buffer.invalid_timestamps == 1 and buffer.resets == 1
    for index in range(61):
        buffer.push(index, index / 25)
    _, samples = buffer.queue.get_nowait()
    assert [sample[0] for sample in samples] == list(range(0, 61, 4))
    assert all(sample[2] == 1 for sample in samples)
    buffer.push("regression", 0)
    assert buffer.resets == 2


def test_preprocessing_matches_original_rgb_aspect_crop_and_normalization():
    helpers = inference_helpers(MODEL_ROOT)
    # Ancho no cuadrado, patrón y canales diferentes detectan deformación y BGR/RGB.
    rng = np.random.default_rng(42)
    bgr = [rng.integers(0, 256, (240, 320, 3), dtype=np.uint8) for _ in range(16)]
    expected = helpers.prepare_clip(bgr, 224, [0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    predictor = VideoMAEPredictor.__new__(VideoMAEPredictor)
    predictor.metadata = load_metadata()
    predictor.helpers = helpers
    captured = []
    predictor._run = lambda tensor, timestamp: captured.append(tensor) or {"timestamp": timestamp}
    predictor.predict_frames([VideoFrame.from_ndarray(frame, format="bgr24") for frame in bgr], 2.4)
    np.testing.assert_array_equal(captured[0], expected)
    assert expected.shape == (1, 3, 16, 224, 224) and expected.dtype == np.float32


def prediction(probabilities):
    return {"probabilities": dict(zip(CLASS_NAMES, probabilities))}


def test_alert_confirmation_release_and_session_isolation():
    metadata = load_metadata()
    helpers = inference_helpers(MODEL_ROOT)
    first = SessionAlertPolicy(metadata, helpers)
    other = SessionAlertPolicy(metadata, helpers)
    crime = [0.05, 0.8, 0.05, 0.05, 0.05]
    normal = [1, 0, 0, 0, 0]
    assert not first.update(prediction(crime), 1)["alert"]["active"]
    result = first.update(prediction(crime), 2)
    assert result["alert"]["started"] and result["alert"]["clase"] == "Hurto"
    assert not other.update(prediction(crime), 2)["alert"]["active"]
    ended = []
    for timestamp in range(3, 10):
        result = first.update(prediction(normal), timestamp)
        if result["alert"]["ended"]:
            ended.append(timestamp)
    assert ended and not result["alert"]["active"]
    assert result["alert"]["start_timestamp"] is None
    first.reset()
    assert not first.update(prediction(crime), 11)["alert"]["active"]


def test_real_startup_fails_without_model_instead_of_using_mock(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("CUDA unavailable")
    monkeypatch.setattr("app.videomae.VideoMAEPredictor", fail)
    with pytest.raises(RuntimeError, match="CUDA unavailable"):
        with TestClient(create_app(mode="videomae", transport="webrtc")):
            pass


def test_dropped_window_breaks_alert_confirmation():
    async def run():
        metadata = load_metadata()
        helpers = inference_helpers(MODEL_ROOT)
        config = VideoMAEConfig(queue_size=1)
        def predict(frames, timestamp):
            return {"clase": "Hurto", "confianza": .8, "timestamp": timestamp,
                    "simulated": False, **prediction([.05, .8, .05, .05, .05])}
        receiver = WebRTCReceiver(None, config, buffer_factory=VideoMAEBuffer, predict_frames=predict)
        session = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])), source_id="test",
                          buffer=VideoMAEBuffer(config), alert_policy=SessionAlertPolicy(metadata, helpers))
        receiver.sessions[session.id] = session
        receiver.spawn(session, receiver.process_windows(session))
        try:
            for index in range(61):
                session.buffer.push(index, index / 25)
            async with asyncio.timeout(2):
                while session.windows_processed < 1:
                    await asyncio.sleep(.01)
            assert not session.latest_result["alert"]["active"]
            # Sin ceder el bucle: llenar cola y descartar la ventana 2.
            for index in range(61, 189):
                session.buffer.push(index, index / 25)
            async with asyncio.timeout(2):
                while session.windows_processed < 2:
                    await asyncio.sleep(.01)
            assert session.buffer.dropped == 1
            assert session.latest_result["window_id"].endswith(":3")
            assert not session.latest_result["alert"]["active"]
            assert session.latest_result["alert"]["candidate_count"] == 1
        finally:
            await receiver.shutdown()
    asyncio.run(run())


def test_old_timeline_result_is_not_published_after_timestamp_reset():
    async def run():
        entered, release = threading.Event(), threading.Event()
        metadata = load_metadata()
        helpers = inference_helpers(MODEL_ROOT)
        config = VideoMAEConfig()
        def predict(frames, timestamp):
            entered.set()
            release.wait(3)
            return {"clase": "Hurto", "confianza": .8, "timestamp": timestamp,
                    "simulated": False, **prediction([.05, .8, .05, .05, .05])}
        receiver = WebRTCReceiver(None, config, buffer_factory=VideoMAEBuffer, predict_frames=predict)
        session = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])), source_id="test",
                          buffer=VideoMAEBuffer(config), alert_policy=SessionAlertPolicy(metadata, helpers))
        receiver.sessions[session.id] = session
        receiver.spawn(session, receiver.process_windows(session))
        try:
            for index in range(61):
                session.buffer.push(index, index / 25)
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.01)
            session.buffer.push("new timeline", 0)
            release.set()
            async with asyncio.timeout(2):
                await session.buffer.queue.join()
            assert session.latest_result is None
            assert session.windows_processed == 0
            assert not session.alert_policy.policy.history
        finally:
            release.set()
            await receiver.shutdown()
    asyncio.run(run())


@pytest.mark.skipif(os.getenv("SENTRIX_TEST_GPU") != "1", reason="Requiere GPU NVIDIA; activar SENTRIX_TEST_GPU=1")
def test_gpu_webrtc_end_to_end():
    class FileTrack(VideoStreamTrack):
        def __init__(self):
            super().__init__()
            configured = os.getenv("SENTRIX_TEST_VIDEO")
            candidates = sorted((MODEL_ROOT.parent / "frontend/static/videos").glob("*.mp4"))
            video = Path(configured).resolve() if configured else (candidates[0] if candidates else None)
            assert video is not None and video.is_file(), "Definir SENTRIX_TEST_VIDEO con un video existente"
            self.cap = cv2.VideoCapture(str(video))
            assert self.cap.isOpened(), f"No se pudo abrir {video}"
            self.index = 0
            self.start = None

        async def recv(self):
            if self.start is None:
                self.start = time.monotonic()
            await asyncio.sleep(max(0, self.start + self.index / 25 - time.monotonic()))
            ok, bgr = self.cap.read()
            if not ok:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, bgr = self.cap.read()
            assert ok
            frame = VideoFrame.from_ndarray(bgr, format="bgr24")
            frame.pts = self.index * 3600
            frame.time_base = Fraction(1, 90000)
            self.index += 1
            return frame

        def stop(self):
            super().stop()
            self.cap.release()

    async def run():
        app = create_app(mode="videomae", transport="webrtc")
        sender = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        source = FileTrack()
        sender.addTrack(source)
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    health = (await client.get("/health")).json()
                    assert health["model_loaded"] and health["gpu_active"]
                    await sender.setLocalDescription(await sender.createOffer())
                    response = await client.post("/webrtc/offer", json={
                        "type": "offer", "sdp": sender.localDescription.sdp, "source_id": "gpu_test"})
                    assert response.status_code == 200, response.text
                    answer = response.json()
                    await sender.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))
                    async with asyncio.timeout(25):
                        while True:
                            status = (await client.get("/webrtc/sessions/" + answer["session_id"])).json()
                            if status.get("windows_processed", 0) >= 2:
                                break
                            await asyncio.sleep(.1)
                    result = status["latest_result"]
                    assert result["simulated"] is False and result["frame_count"] == 16
                    assert set(result["probabilities"]) == set(CLASS_NAMES)
                    assert sum(result["probabilities"].values()) == pytest.approx(1, abs=1e-6)
                    assert isinstance(result["alert"]["active"], bool)
                    assert status["processing_errors"] == 0
                    assert status["pipeline_config"]["sampling_rate"] == 4
                    assert (await client.delete("/webrtc/sessions/" + answer["session_id"])).status_code == 200
                    assert (await client.get("/webrtc/sessions")).json() == []
        finally:
            await sender.close()
            source.stop()
            await app.state.webrtc.shutdown()
    asyncio.run(run())
