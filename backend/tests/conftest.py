"""Isolated test configuration; never load deployment credentials."""
import pytest


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("SENTRIX_DATABASE_URL", "")
    monkeypatch.setenv("SENTRIX_DETECTION_DB", str(tmp_path / "detections.sqlite3"))
    monkeypatch.setenv("SENTRIX_LOG_DB", str(tmp_path / "logs.sqlite3"))
    monkeypatch.setenv("SENTRIX_CONTROL_API_KEY", "test-control-key")
    monkeypatch.setenv("SENTRIX_PREDICTOR", "mock")
    monkeypatch.setenv("SENTRIX_VIDEO_TRANSPORT", "rtsp")
    for key in ("PROTOCOL", "HOST", "PORT", "USER", "PASSWORD", "SHA256"):
        monkeypatch.delenv("SENTRIX_RTSP_" + key, raising=False)
    for key in ("SENTRIX_SOURCE_ID", "SENTRIX_STREAM_PATH", "SENTRIX_CAMERA_NAME"):
        monkeypatch.delenv(key, raising=False)
    from app.infrastructure.persistence.detections import DetectionStore
    return DetectionStore(tmp_path / "detections.sqlite3")
