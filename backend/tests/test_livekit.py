import asyncio
import os
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import jwt
import numpy as np
import pytest
from fastapi.testclient import TestClient
from livekit import rtc

from app.livekit_receiver import LiveKitSettings, LiveKitReceiver, LiveKitFrameTrack
from app.main import create_app
from app.pipeline import VideoMAEBuffer, VideoMAEConfig
from app.videomae import load_metadata, inference_helpers, MODEL_ROOT, SessionAlertPolicy


def settings(**overrides):
    defaults = dict(url="ws://127.0.0.1:7880", room="sentrix-test", source_id="camara_gabriel",
                    participant_identity="gabriel", api_key="test-key", api_secret="s" * 32)
    return LiveKitSettings(**{**defaults, **overrides})


class Publication:
    def __init__(self, sid="video-1", kind=rtc.TrackKind.KIND_VIDEO):
        self.sid = sid
        self.kind = kind
        self.track = object()
        self.muted = False
        self.subscribed = False
        self.subscription_calls = []

    def set_subscribed(self, value):
        self.subscribed = value
        self.subscription_calls.append(value)


def participant(identity, publications):
    return SimpleNamespace(identity=identity, track_publications={item.sid: item for item in publications})


class Room:
    def __init__(self, participants=()):
        self.remote_participants = {item.identity: item for item in participants}
        self.events = {}
        self.connected = False
        self.options = None
        self.disconnected = False

    def on(self, event, callback=None):
        def register(fn):
            self.events.setdefault(event, []).append(fn)
            return fn
        return register(callback) if callback else register

    def emit(self, event, *args):
        for callback in self.events.get(event, []):
            callback(*args)

    async def connect(self, url, token, *, options):
        self.connected = True
        self.options = options

    async def disconnect(self):
        self.disconnected = True


class Stream:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.closed = False

    async def __anext__(self):
        event = await self.queue.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def aclose(self):
        self.closed = True
        self.queue.put_nowait(None)


def frame_event(index=0, rotation=0):
    rgb = np.zeros((2, 4, 3), dtype=np.uint8)
    rgb[:, :, 0] = 255
    frame = rtc.VideoFrame(4, 2, rtc.VideoBufferType.RGB24, rgb.tobytes())
    return SimpleNamespace(frame=frame, timestamp_us=1_000_000 + index * 40_000, rotation=rotation)


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.01)


def test_no_configuration_keeps_api_available_and_discloses_missing_values():
    with TestClient(create_app(mode="mock", transport="livekit", livekit_settings=LiveKitSettings())) as client:
        health = client.get("/health").json()
        assert health["video_transport"] == "livekit"
        assert health["livekit"]["state"] == "missing_configuration"
        assert "LIVEKIT_URL" in health["livekit"]["missing_configuration"]
        assert client.get("/webrtc/sessions").json() == []
        assert client.post("/webrtc/offer", json={}).status_code == 404


@pytest.mark.parametrize("change", [
    {"url": "https://example.com"}, {"url": "wss://user:secret@example.com"},
    {"url": "wss://example.com?token=secret"}, {"source_id": "x" * 101},
    {"participant_identity": "sentrix-inference"},
])
def test_invalid_settings(change):
    with pytest.raises(ValueError):
        settings(**change)


def test_generated_token_is_subscriber_only_and_secrets_are_not_disclosed():
    config = settings()
    claims = jwt.decode(config.access_token(), config.api_secret, algorithms=["HS256"])
    assert claims["sub"] == "sentrix-inference"
    assert claims["video"]["room"] == "sentrix-test"
    assert claims["video"]["canSubscribe"] is True
    assert claims["video"]["canPublish"] is False
    assert claims["video"]["canPublishData"] is False
    receiver = LiveKitReceiver(None, settings=config)
    assert config.api_secret not in repr(config)
    assert config.api_secret not in str(receiver.connection_status())


def test_external_token_checks_room_and_expiration_without_leaking_token():
    config = settings()
    token = config.access_token()
    assert replace(config, token=token).access_token() == token
    with pytest.raises(ValueError, match="LIVEKIT_TOKEN inválido"):
        replace(config, room="wrong-room", token=token).access_token()
    expired = jwt.encode({"exp": time.time() - 60, "sub": "receiver"}, "s" * 32, algorithm="HS256")
    with pytest.raises(ValueError):
        replace(config, token=expired).access_token()


def test_native_livekit_frame_conversion_and_timestamp_origin():
    async def run():
        stream = Stream()
        adapter = LiveKitFrameTrack(stream)
        stream.queue.put_nowait(frame_event(0))
        first = await adapter.recv()
        np.testing.assert_array_equal(first.to_ndarray(format="rgb24")[0, 0], [255, 0, 0])
        assert first.time == 0 and first.width == 4 and first.height == 2
        stream.queue.put_nowait(frame_event(1, rotation=1))
        second = await adapter.recv()
        assert second.time == .04 and second.width == 2 and second.height == 4
        await adapter.aclose()
        assert stream.closed
    asyncio.run(run())


def test_sdk_room_event_registration_and_rgba_frame_conversion():
    async def run():
        # Instanciar el SDK real sin conectarse a ninguna red/sala.
        room = rtc.Room()
        receiver = LiveKitReceiver(None, settings=LiveKitSettings())
        receiver.room = room
        receiver._bind_events(room, asyncio.Event())
        rgba = np.zeros((2, 4, 4), dtype=np.uint8)
        rgba[:, :, 1] = 255
        rgba[:, :, 3] = 255
        stream = Stream()
        stream.queue.put_nowait(SimpleNamespace(
            frame=rtc.VideoFrame(4, 2, rtc.VideoBufferType.RGBA, rgba.tobytes()).convert(rtc.VideoBufferType.I420),
            timestamp_us=0, rotation=0))
        adapter = LiveKitFrameTrack(stream)
        converted = await adapter.recv()
        np.testing.assert_allclose(converted.to_ndarray(format="rgb24")[0, 0], [0, 255, 0], atol=2)
        await adapter.aclose()
        await room.disconnect()
    asyncio.run(run())


def test_only_configured_publisher_video_is_processed_and_api_matches_mari():
    async def run():
        target, audio, stranger = Publication(), Publication("audio", rtc.TrackKind.KIND_AUDIO), Publication("other-video")
        room = Room([participant("gabriel", [target, audio]), participant("stranger", [stranger])])
        stream = Stream()
        config = VideoMAEConfig()
        helpers = inference_helpers(MODEL_ROOT)
        def predict(frames, timestamp):
            assert len(frames) == 16
            return {"clase": "Normal", "confianza": .8, "timestamp": timestamp, "simulated": False,
                    "probabilities": dict(zip(["normal", "hurto", "robo", "agresion_fisica", "vandalismo"],
                                              [.8, .05, .05, .05, .05]))}
        receiver = LiveKitReceiver(None, config, settings=settings(), room_factory=lambda: room,
            stream_factory=lambda track: stream, buffer_factory=VideoMAEBuffer, predict_frames=predict,
            policy_factory=lambda: SessionAlertPolicy(load_metadata(), helpers))
        try:
            await receiver.start()
            await wait_for(lambda: bool(receiver.sessions))
            assert room.options.auto_subscribe is False
            assert target.subscribed and not audio.subscription_calls and not stranger.subscription_calls
            session = next(iter(receiver.sessions.values()))
            assert session.snapshot()["connection_state"] == "connecting"
            for index in range(61):
                stream.queue.put_nowait(frame_event(index))
            await wait_for(lambda: session.windows_processed == 1)
            result = session.snapshot()
            assert result["connection_state"] == "connected"
            assert result["source_id"] == "camara_gabriel" and result["frames_received"] == 61
            assert result["latest_result"]["session_id"] == result["session_id"]
            assert result["latest_result"]["frame_count"] == 16
            assert result["latest_result"]["timestamp"] == 2.4
            assert result["latest_result"]["simulated"] is False
            assert result["latest_result"]["alert"]["active"] is False
            room.emit("reconnecting")
            assert session.latest_result is None
            await wait_for(lambda: not receiver.sessions)
            # Misma publicación tras reconexión: UUID nuevo y política vacía.
            receiver.stream_factory = lambda track: Stream()
            room.emit("reconnected")
            await wait_for(lambda: bool(receiver.sessions))
            replacement = next(iter(receiver.sessions.values()))
            assert replacement.id != session.id and replacement.latest_result is None
            assert not replacement.alert_policy.policy.history
        finally:
            await receiver.shutdown()
        assert stream.closed and room.disconnected and not receiver.sessions
        assert not receiver._events and receiver._runner is None
    asyncio.run(run())


def test_ambiguous_tracks_are_rejected_and_departure_removes_source():
    async def run():
        first, second = Publication(), Publication("video-2")
        person = participant("gabriel", [first, second])
        room = Room([person])
        receiver = LiveKitReceiver(None, settings=settings(), room_factory=lambda: room,
                                   stream_factory=lambda track: Stream())
        try:
            await receiver.start()
            await wait_for(lambda: receiver.state == "ambiguous_video")
            assert not receiver.sessions and first.subscription_calls == [False]
            del person.track_publications[second.sid]
            room.emit("track_unpublished", second, person)
            await wait_for(lambda: bool(receiver.sessions))
            room.remote_participants.clear()
            room.emit("participant_disconnected", person)
            await wait_for(lambda: receiver.state == "waiting_publisher")
            assert not receiver.sessions
        finally:
            await receiver.shutdown()
    asyncio.run(run())


def test_connection_errors_are_sanitized_and_shutdown_cancels_retry():
    class FailingRoom(Room):
        async def connect(self, url, token, **kwargs):
            raise RuntimeError("private-token-should-not-leak")
    async def run():
        receiver = LiveKitReceiver(None, settings=settings(), room_factory=FailingRoom)
        await receiver.start()
        await wait_for(lambda: receiver.state == "connection_error")
        assert "private-token" not in str(receiver.connection_status())
        await receiver.shutdown()
        assert receiver._runner is None and not receiver.sessions
    asyncio.run(run())


@pytest.mark.skipif(os.getenv("SENTRIX_TEST_GPU") != "1", reason="Requiere GPU; no requiere sala LiveKit real")
def test_gpu_livekit_frames_and_http_results():
    async def run():
        app = create_app(mode="videomae", transport="livekit", livekit_settings=settings())
        receiver = app.state.video_receiver
        room = Room([participant("gabriel", [Publication()])])
        stream = Stream()
        receiver.room_factory = lambda: room
        receiver.stream_factory = lambda track: stream
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/health")).json()["gpu_active"]
                await wait_for(lambda: bool(receiver.sessions))
                for index in range(61):
                    stream.queue.put_nowait(frame_event(index))
                session = next(iter(receiver.sessions.values()))
                await wait_for(lambda: session.windows_processed == 1)
                listing = (await client.get("/webrtc/sessions")).json()
                detail = (await client.get("/webrtc/sessions/" + listing[0]["session_id"])).json()
                assert detail["connection_state"] == "connected"
                assert detail["latest_result"]["simulated"] is False
                assert detail["latest_result"]["session_id"] == detail["session_id"]
                assert detail["latest_result"]["source_id"] == settings().source_id
                assert sum(detail["latest_result"]["probabilities"].values()) == pytest.approx(1)
                assert detail["processing_errors"] == 0
    asyncio.run(run())
