"""API tests: create/run/list workflows and the approval flow over HTTP."""
import pytest
from fastapi.testclient import TestClient

from workflow_engine.api import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "api_runs.db"))
    with TestClient(app) as test_client:
        yield test_client


SIMPLE = {
    "name": "hello",
    "steps": [
        {"id": "greet", "type": "transform", "config": {"set": {"msg": "hi"}}},
    ],
}

GATED = {
    "name": "gated",
    "steps": [
        {"id": "review", "type": "human_approval", "config": {"message": "ok?"}},
        {"id": "after", "type": "transform", "depends_on": ["review"],
         "config": {"set": {"done": True}}},
    ],
}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_run_workflow_via_api(client):
    resp = client.post("/runs", json={"definition": SIMPLE, "inputs": {}})
    assert resp.status_code == 201
    run = resp.json()
    assert run["status"] == "completed"
    assert run["context"]["greet"] == {"msg": "hi"}

    listed = client.get("/runs").json()
    assert any(r["id"] == run["id"] for r in listed)
    fetched = client.get(f"/runs/{run['id']}").json()
    assert fetched["workflow_name"] == "hello"


def test_approval_flow_via_api(client):
    resp = client.post("/runs", json={"definition": GATED, "inputs": {}})
    run = resp.json()
    assert run["status"] == "awaiting_approval"

    resp = client.post(f"/runs/{run['id']}/approve", json={"approved": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_invalid_definition_is_422(client):
    resp = client.post("/runs", json={"definition": {"steps": []}, "inputs": {}})
    assert resp.status_code == 422


def test_missing_run_is_404(client):
    assert client.get("/runs/does-not-exist").status_code == 404


def test_examples_are_listed_and_runnable(client):
    names = client.get("/examples").json()
    assert "document_triage" in names
    resp = client.post(
        "/examples/document_triage/run",
        json={"inputs": {"text": "The app shows an error on login"}},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "completed"
