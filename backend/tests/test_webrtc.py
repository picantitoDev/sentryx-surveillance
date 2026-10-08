import asyncio
import httpx
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription, VideoStreamTrack
from fastapi.testclient import TestClient
from app.main import create_app


def test_invalid_offer_cleans_session():
    with TestClient(create_app(mode="mock", transport="webrtc")) as client:
        assert client.post('/webrtc/offer', json={'type': 'offer', 'sdp': 'invalid'}).status_code == 400
        assert client.get('/webrtc/sessions').json() == []
        assert client.get('/webrtc/sessions/missing').status_code == 404
        assert client.delete('/webrtc/sessions/missing').status_code == 404


def test_cors(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://localhost:5173,http://frontend.example:5173")
    with TestClient(create_app(mode="mock", transport="webrtc")) as client:
        response = client.options('/webrtc/offer', headers={
            'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST'})
        assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
        response = client.options('/webrtc/sessions', headers={
            'Origin': 'http://frontend.example:5173', 'Access-Control-Request-Method': 'GET'})
        assert response.headers['access-control-allow-origin'] == 'http://frontend.example:5173'
        response = client.options('/webrtc/offer', headers={
            'Origin': 'http://unconfigured.example', 'Access-Control-Request-Method': 'POST'})
        assert response.status_code == 400


def test_real_webrtc_frames_and_cleanup():
    async def run():
        app = create_app(mode="mock", transport="webrtc")
        sender = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        sender.addTrack(VideoStreamTrack())
        viewers = []
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    await sender.setLocalDescription(await sender.createOffer())
                    offer = {'type': 'offer', 'sdp': sender.localDescription.sdp, 'source_id': 'test_camera'}
                    response = await client.post('/webrtc/offer', json=offer)
                    assert response.status_code == 200, response.text
                    answer = response.json()
                    assert (await client.post('/webrtc/offer', json=offer)).status_code == 409
                    await sender.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'], type=answer['type']))
                    async with asyncio.timeout(15):
                        while True:
                            status = (await client.get('/webrtc/sessions/' + answer['session_id'])).json()
                            if status.get('windows_processed', 0) >= 2:
                                break
                            await asyncio.sleep(0.1)
                    assert status['width'] == 640 and status['height'] == 480
                    assert status['connection_state'] == 'connected'
                    assert status['source_id'] == 'test_camera'
                    result = status['latest_result']
                    assert result['simulated'] is True and result['confianza'] == 0.75
                    assert result['session_id'] == answer['session_id']
                    assert result['frame_count'] == 8
                    assert result['window_end'] > result['window_start']
                    assert status['processing_errors'] == 0
                    for cycle in range(2):
                        viewer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                        viewers.append(viewer)
                        received = asyncio.get_running_loop().create_future()
                        @viewer.on('track')
                        def on_track(track):
                            if not received.done():
                                received.set_result(track)
                        viewer.addTransceiver('video', direction='recvonly')
                        await viewer.setLocalDescription(await viewer.createOffer())
                        response = await client.post('/webrtc/viewers/offer', json={
                            'type': 'offer', 'sdp': viewer.localDescription.sdp,
                            'session_id': answer['session_id']})
                        assert response.status_code == 200, response.text
                        remote = response.json()
                        await viewer.setRemoteDescription(RTCSessionDescription(sdp=remote['sdp'], type='answer'))
                        track = await asyncio.wait_for(received, 5)
                        frame = await asyncio.wait_for(track.recv(), 10)
                        assert frame.width == 640 and frame.height == 480
                        if cycle == 0:
                            assert (await client.delete('/webrtc/viewers/' + remote['viewer_id'])).status_code == 200
                            assert (await client.get('/webrtc/sessions/' + answer['session_id'])).status_code == 200
                    assert (await client.delete('/webrtc/sessions/' + answer['session_id'])).status_code == 200
                    assert (await client.get('/webrtc/sessions')).json() == []
                    assert (await client.get('/webrtc/viewers/' + remote['viewer_id'])).status_code == 404
                    assert not app.state.webrtc.viewers
        finally:
            for viewer in viewers:
                await viewer.close()
            await sender.close()
            await app.state.webrtc.shutdown()
    asyncio.run(run())
