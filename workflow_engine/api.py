"""Small FastAPI layer over the workflow engine.

Run:  uvicorn workflow_engine.api:app --port 8000
State lives in a SQLite file (WORKFLOW_DB_PATH env, default workflow_runs.db).
"""
from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from workflow_engine.engine import WorkflowEngine, load_definition_file
from workflow_engine.models import WorkflowDefinition
from workflow_engine.store import RunStore

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


class RunRequest(BaseModel):
    definition: dict
    inputs: dict = {}


class ApproveRequest(BaseModel):
    approved: bool


class InputsRequest(BaseModel):
    inputs: dict = {}


def create_app(db_path: str | None = None) -> FastAPI:
    engine = WorkflowEngine(
        RunStore(db_path or os.environ.get("WORKFLOW_DB_PATH", "workflow_runs.db"))
    )
    app = FastAPI(title="AI Workflow Automation Platform", version="1.0.0")
    app.state.engine = engine

    def _run_out(run) -> dict:
        return asdict(run)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/runs", status_code=201)
    def start_run(payload: RunRequest):
        try:
            definition = WorkflowDefinition.from_dict(payload.definition)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        run = engine.start_run(definition, payload.inputs)
        return _run_out(run)

    @app.get("/runs")
    def list_runs():
        return [_run_out(r) for r in engine.list_runs()]

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        run = engine.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No such run: {run_id}")
        return _run_out(run)

    @app.post("/runs/{run_id}/approve")
    def approve(run_id: str, payload: ApproveRequest):
        try:
            run = engine.approve(run_id, payload.approved)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _run_out(run)

    @app.get("/examples")
    def list_examples():
        return sorted(p.stem for p in EXAMPLES_DIR.glob("*.yaml"))

    @app.post("/examples/{name}/run", status_code=201)
    def run_example(name: str, payload: InputsRequest):
        path = EXAMPLES_DIR / f"{name}.yaml"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No such example: {name}")
        definition = load_definition_file(path)
        run = engine.start_run(definition, payload.inputs)
        return _run_out(run)

    return app


app = create_app()
