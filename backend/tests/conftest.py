import os
from pathlib import Path
import pytest

# Las pruebas no deben heredar Neon ni credenciales del entorno de ejecución.
os.environ["SENTRIX_DATABASE_URL"] = ""
os.environ["SENTRIX_CONTROL_API_KEY"] = "test-control-key"
os.environ["SENTRIX_VIDEO_TRANSPORT"] = "webrtc"
# Permite importar app.main sin exigir un servidor en la suite SQLite.
os.environ.setdefault("SENTRIX_DETECTION_DB", str(Path(__file__).resolve().parents[1] / "data/test-bootstrap.sqlite3"))


@pytest.fixture(autouse=True)
def isolated_detection_database(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTRIX_DATABASE_URL", raising=False)
    monkeypatch.setenv("SENTRIX_CONTROL_API_KEY", "test-control-key")
    monkeypatch.setenv("SENTRIX_VIDEO_TRANSPORT", "webrtc")
    monkeypatch.setenv("SENTRIX_DETECTION_DB", str(tmp_path / "detections.sqlite3"))
    monkeypatch.setenv("SENTRIX_LOG_DB", str(tmp_path / "inference_logs.sqlite3"))


@pytest.fixture(autouse=True)
def authenticated_test_clients(monkeypatch):
    # Las rutas de datos usan la misma clave que el servidor SvelteKit.
    from fastapi.testclient import TestClient
    original_init = TestClient.__init__
    def initialize(self, *args, headers=None, **kwargs):
        headers = {"X-API-Key": os.environ["SENTRIX_CONTROL_API_KEY"], **(headers or {})}
        original_init(self, *args, headers=headers, **kwargs)
    monkeypatch.setattr(TestClient, "__init__", initialize)
