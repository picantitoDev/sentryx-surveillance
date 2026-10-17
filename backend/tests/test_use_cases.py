"""Use cases run with in-memory ports, without FastAPI, SQL or GPU."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.application.cameras import CameraService
from app.application.errors import Conflict, InvalidInput, NotFound, Unavailable
from app.application.inference import InferenceService
from app.application.queries import RecordQueries
from app.domain.camera import CameraRegistration
from app.domain.inference import Prediction, Window


class MemoryCameras:
    def __init__(self):
        self.cameras = {}

    def get(self, identifier):
        return self.cameras.get(identifier)

    def list(self):
        return list(self.cameras.values())

    def save(self, camera, *, update=False):
        if (camera.source_id in self.cameras) != update:
            return False
        self.cameras[camera.source_id] = camera
        return True

    def delete(self, identifier):
        return self.cameras.pop(identifier, None) is not None


class MemorySources:
    def __init__(self):
        self.sessions = {}
        self.opened = 0

    def active(self, identifier):
        return self.sessions.get(identifier)

    def snapshot(self, session):
        return {"source_id": session.id, "active": True}

    def open(self, camera):
        self.opened += 1
        session = SimpleNamespace(id=camera.source_id)
        self.sessions[camera.source_id] = session
        return self.snapshot(session)

    async def close(self, identifier):
        self.sessions.pop(identifier)


def test_camera_registration_and_lifecycle_use_only_ports():
    async def exercise():
        repository, sources = MemoryCameras(), MemorySources()
        default = CameraRegistration(source_id="default", password="private-test-password")
        service = CameraService(repository, sources, default, asyncio.Lock())
        body = CameraRegistration(source_id="new")
        camera = await service.register_camera(body)
        assert camera["stream_path"] == "new"
        assert camera["name"] == "new"
        assert camera["has_password"] is True and "password" not in camera
        with pytest.raises(Conflict):
            await service.register_camera(body)
        start = SimpleNamespace(source_id="new", stream_path=None)
        assert await service.start_source(start) == await service.start_source(start)
        assert sources.opened == 1
        with pytest.raises(Conflict):
            await service.update_camera("new", body)
        with pytest.raises(Conflict):
            await service.delete_camera("new")
        await service.stop_source("new")
        assert (await service.delete_camera("new"))["deleted"] is True
        with pytest.raises(NotFound):
            await service.get_camera("new")

    asyncio.run(exercise())


def test_camera_storage_failure_is_an_application_error():
    class BrokenCameras(MemoryCameras):
        def list(self):
            raise RuntimeError("private SQL diagnostic")

    async def exercise():
        service = CameraService(BrokenCameras(), MemorySources(), None, asyncio.Lock())
        with pytest.raises(Unavailable, match="Registro de camaras no disponible"):
            await service.list_cameras()

    asyncio.run(exercise())


def test_queries_distinguish_missing_storage_failure_and_invalid_dates():
    class Repository:
        def get(self, identifier):
            return None

        def list(self, **filters):
            raise RuntimeError("private SQL diagnostic")

    queries = RecordQueries(Repository(), unavailable="Unavailable", missing="Missing")
    with pytest.raises(NotFound, match="Missing"):
        queries.get("absent")
    with pytest.raises(Unavailable, match="Unavailable"):
        queries.list()
    with pytest.raises(InvalidInput):
        queries.list(date_from=datetime(2026, 2, 1, tzinfo=timezone.utc),
                     date_to=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_inference_saves_validated_prediction_and_returns_detection_id():
    events, saved = [], []
    window = Window(timestamp=1, frames=[[[[0., 0., 0.]]]])

    class Model:
        def predict(self, value):
            assert value is window
            return Prediction(clase="Normal", confianza=.9, timestamp=1, simulated=True)

    class Repository:
        def save(self, result, camera):
            saved.append((result, camera))
            return {"id": "detection-1"}

    logs = SimpleNamespace(emit=lambda *args, **kwargs: events.append((args, kwargs)))
    service = InferenceService(lambda: Model(), Repository(), logs)
    result, identifier = service.predict(window)
    assert identifier == "detection-1" and result.clase == "Normal"
    assert saved[0][1] == window.source_id
    assert events[0][0][0] == "inference_completed"
    with pytest.raises(InvalidInput):
        InferenceService(lambda: Model(), Repository(), logs, real_mode=True).predict(window)


@pytest.mark.parametrize("stage", ["inference", "storage"])
def test_inference_failures_keep_operational_event_and_hide_internal_error(stage):
    events = []

    class Model:
        def predict(self, window):
            if stage == "inference":
                raise RuntimeError("private model diagnostic")
            return Prediction(clase="Normal", confianza=.9, timestamp=1, simulated=True)

    class Repository:
        def save(self, *args):
            raise RuntimeError("private database diagnostic")

    logs = SimpleNamespace(emit=lambda event, *args, **kwargs: events.append(event))
    service = InferenceService(lambda: Model(), Repository(), logs)
    with pytest.raises(Unavailable) as failure:
        service.predict(Window(timestamp=1, frames=[[[[0., 0., 0.]]]]))
    assert "private" not in str(failure.value)
    assert events == ["inference_error" if stage == "inference" else "detection_storage_error"]
