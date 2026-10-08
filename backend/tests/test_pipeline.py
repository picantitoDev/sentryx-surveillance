import asyncio
import threading
from fractions import Fraction

import pytest
from av import VideoFrame
from aiortc import RTCPeerConnection, RTCConfiguration
from app.pipeline import PipelineConfig, WindowBuffer, prepare_frames
from app.webrtc import WebRTCReceiver, Session


def rgb_frame():
    frame = VideoFrame(2, 2, 'rgb24')
    plane = frame.planes[0]
    raw = bytearray(plane.buffer_size)
    for y in range(2):
        raw[y * plane.line_size:y * plane.line_size + 6] = bytes([255, 0, 128, 0, 255, 0])
    plane.update(bytes(raw))
    return frame


def test_rgb_normalization_and_resize():
    config = PipelineConfig(width=2, height=2)
    pixels = prepare_frames([rgb_frame()], config)
    assert pixels[0][0][0] == [1.0, 0.0, 128 / 255]
    assert pixels[0][1][1] == [0.0, 1.0, 0.0]
    resized = prepare_frames([rgb_frame()], PipelineConfig(width=4, height=3))
    assert len(resized[0]) == 3 and len(resized[0][0]) == 4


def test_sampling_and_non_overlapping_windows():
    buffer = WindowBuffer(PipelineConfig())
    for i in range(60):
        buffer.push(i, i / 15)
    assert buffer.created == 2
    _, first = buffer.queue.get_nowait()
    _, second = buffer.queue.get_nowait()
    assert len(first) == len(second) == 8
    assert first[-1][1] < second[0][1]
    assert not set(x[0] for x in first) & set(x[0] for x in second)
    assert buffer.sampled == 16


def test_bounded_queue_keeps_recent_window():
    buffer = WindowBuffer(PipelineConfig(window_size=2, queue_size=1))
    for i in range(6):
        buffer.push(i, i / 4)
    assert buffer.created == 3 and buffer.dropped == 2
    assert buffer.queue.qsize() == 1
    number, samples = buffer.queue.get_nowait()
    assert number == 3 and samples[0][0] == 4


def test_timestamp_reset_and_session_isolation():
    config = PipelineConfig(window_size=2)
    buffer = WindowBuffer(config)
    buffer.push('old', 4)
    buffer.push('new', 0)
    buffer.push('new2', .25)
    assert buffer.resets == 1
    assert [x[0] for x in buffer.queue.get_nowait()[1]] == ['new', 'new2']
    buffer.push('invalid', None)
    assert buffer.invalid_timestamps == 1
    buffer.push('partial', .5)
    buffer.clear()
    assert not buffer.partial and buffer.queue.empty()
    assert WindowBuffer(config).sampled == 0


@pytest.mark.parametrize('kwargs', [{'fps': 0}, {'fps': float('nan')}, {'width': 225},
                                   {'window_size': 33}, {'queue_size': 0}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        PipelineConfig(**kwargs)


def test_slow_predictor_does_not_block_and_recovers_after_error():
    async def run():
        entered, release = threading.Event(), threading.Event()
        calls = 0
        def predict(frames, timestamp):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                release.wait(3)
                raise RuntimeError('test failure')
            return {'clase': 'Hurto', 'confianza': .75, 'timestamp': timestamp, 'simulated': True}
        config = PipelineConfig(width=2, height=2, window_size=1, queue_size=1)
        receiver = WebRTCReceiver(predict, config)
        session = Session(pc=RTCPeerConnection(RTCConfiguration(iceServers=[])),
                          source_id='test', buffer=WindowBuffer(config))
        receiver.sessions[session.id] = session
        receiver.spawn(session, receiver.process_windows(session))
        try:
            session.buffer.push(rgb_frame(), 0)
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.01)
            # El bucle de recepción sigue disponible mientras el predictor espera.
            session.buffer.push(rgb_frame(), .25)
            session.buffer.push(rgb_frame(), .5)
            assert session.buffer.dropped == 1
            release.set()
            async with asyncio.timeout(3):
                while session.windows_processed < 1:
                    await asyncio.sleep(.01)
            assert session.processing_errors == 1
            assert session.latest_result['window_end'] == .5
            assert session.latest_result['session_id'] == session.id
            assert session.last_error is None
        finally:
            release.set()
            await receiver.shutdown()
        assert session.buffer.queue.empty() and not session.buffer.partial
        assert session.latest_result is None
    asyncio.run(run())
