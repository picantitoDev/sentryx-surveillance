import os
from pathlib import Path
import pytest

# Permite importar app.main sin exigir un servidor en la suite SQLite.
os.environ.setdefault("SENTRIX_DETECTION_DB", str(Path(__file__).resolve().parents[1] / "data/test-bootstrap.sqlite3"))


@pytest.fixture(autouse=True)
def isolated_detection_database(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTRIX_DATABASE_URL", raising=False)
    monkeypatch.setenv("SENTRIX_DETECTION_DB", str(tmp_path / "detections.sqlite3"))
    monkeypatch.setenv("SENTRIX_LOG_DB", str(tmp_path / "inference_logs.sqlite3"))
