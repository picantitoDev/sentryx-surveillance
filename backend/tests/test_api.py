import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app, MockPredictor

@pytest.fixture
def client():
    with TestClient(create_app(mode="mock", transport="webrtc")) as client:
        yield client

@pytest.fixture
def payload():
    return json.loads((Path(__file__).resolve().parents[1] / "examples/request.json").read_text())

def test_valid_deterministic_prediction(client, payload):
    response = client.post("/predict", json=payload)
    assert response.status_code == 200
    assert response.json() == {"clase": "Hurto", "confianza": 0.75,
                               "timestamp": 1.5, "simulated": True}
    assert client.post("/predict", json=payload).json() == response.json()

@pytest.mark.parametrize("change", [
    {"frames": []}, {"frames": [[]]}, {"frames": [[[[0, 1]]]]},
    {"frames": [[[[0, 0, 2]]]]}, {"timestamp": -1},
    {"timestamp": "1.5"}, {"extra": True},
    {"frames": [[[[0, 0, 0]]], [[[0, 0, 0], [1, 1, 1]]]]},
])
def test_invalid_then_valid(client, payload, change):
    assert client.post("/predict", json={**payload, **change}).status_code == 422
    assert client.post("/predict", json=payload).status_code == 200

def test_missing_and_malformed(client):
    assert client.post("/predict", json={}).status_code == 422
    assert client.post("/predict", content="broken", headers={"Content-Type": "application/json"}).status_code == 422

def test_body_limit(client):
    assert client.post("/predict", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert client.get("/health").status_code == 200

def test_predictor_failure_and_recovery(payload):
    class Failing:
        def predict(self, window):
            raise RuntimeError("internal detail")
    app = create_app(Failing())
    with TestClient(app) as client:
        response = client.post("/predict", json=payload)
        assert response.status_code == 503
        assert response.json() == {"detail": "Predictor no disponible"}
        app.state.predictor = MockPredictor()
        assert client.post("/predict", json=payload).status_code == 200

def test_health_discloses_mock(client):
    assert client.get("/health").json()["model_loaded"] is False
