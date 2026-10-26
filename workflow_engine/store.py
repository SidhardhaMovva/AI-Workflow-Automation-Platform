"""SQLite persistence for workflow runs.

One row per run; the full run record (definition, context, per-step state)
is stored as JSON so a paused run — e.g. one waiting on human approval —
can be resumed in a later process exactly where it stopped.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from workflow_engine.models import RunRecord


class RunStore:
    def __init__(self, db_path: str | Path = "workflow_runs.db"):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    workflow_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def save(self, run: RunRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, workflow_name, status, record_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    record_json=excluded.record_json,
                    updated_at=excluded.updated_at
                """,
                (
                    run.id,
                    run.workflow_name,
                    run.status,
                    json.dumps(run.to_dict()),
                    run.created_at,
                    run.updated_at,
                ),
            )

    def get(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return RunRecord.from_dict(json.loads(row[0]))

    def list(self, limit: int = 100) -> list[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT record_json FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [RunRecord.from_dict(json.loads(r[0])) for r in rows]
