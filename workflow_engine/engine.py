"""The workflow engine: DAG ordering, execution, retries, approvals.

Execution is synchronous and in-process. A run moves through steps in
topological (dependency) order; ``run_if`` gates can skip a step; a
``human_approval`` step pauses the run and persists it, and
:meth:`WorkflowEngine.approve` resumes it.
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from workflow_engine.models import (
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    WorkflowDefinition,
)
from workflow_engine.providers import MockProvider
from workflow_engine.steps import BUILTIN_STEPS
from workflow_engine.store import RunStore


def load_definition_file(path: str | Path) -> WorkflowDefinition:
    text = Path(path).read_text()
    if Path(path).suffix in (".yaml", ".yml"):
        raw = yaml.safe_load(text)
    else:
        import json

        raw = json.loads(text)
    return WorkflowDefinition.from_dict(raw)


def topological_order(definition: WorkflowDefinition) -> list[str]:
    """Kahn's algorithm over depends_on; preserves definition order for ties."""
    ids = [s.id for s in definition.steps]
    deps = {s.id: set(s.depends_on) for s in definition.steps}
    ordered: list[str] = []
    ready = [sid for sid in ids if not deps[sid]]
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for sid in ids:
            if sid not in ordered and sid not in ready and current in deps[sid]:
                deps[sid].discard(current)
                if not deps[sid]:
                    ready.append(sid)
    if len(ordered) != len(ids):
        cycle = [sid for sid in ids if sid not in ordered]
        raise ValueError(f"Workflow '{definition.name}' has a dependency cycle: {cycle}")
    return ordered


class WorkflowEngine:
    def __init__(self, store: RunStore, providers: dict | None = None):
        self.store = store
        self.providers = providers or {"mock": MockProvider()}
        self.step_executors = dict(BUILTIN_STEPS)

    def register_step_type(self, name: str, executor) -> None:
        """Add a custom step type: executor(step, context, providers) -> output."""
        self.step_executors[name] = executor

    def register_provider(self, name: str, provider) -> None:
        self.providers[name] = provider

    # -- public API ---------------------------------------------------------

    def start_run(
        self, definition: WorkflowDefinition, inputs: dict | None = None
    ) -> RunRecord:
        run = RunRecord(
            workflow_name=definition.name,
            definition={
                "name": definition.name,
                "description": definition.description,
                "steps": [
                    {
                        "id": s.id,
                        "type": s.type,
                        "config": s.config,
                        "depends_on": s.depends_on,
                        "run_if": s.run_if,
                        "retries": s.retries,
                        "retry_delay_seconds": s.retry_delay_seconds,
                    }
                    for s in definition.steps
                ],
            },
            inputs=inputs or {},
            status=RunStatus.RUNNING.value,
        )
        run.context = {"inputs": dict(run.inputs)}
        run.steps = {
            s.id: StepRecord(step_id=s.id, type=s.type) for s in definition.steps
        }
        self._save(run)
        self._execute(run, definition)
        return run

    def approve(self, run_id: str, approved: bool) -> RunRecord:
        run = self._require_run(run_id)
        if run.status != RunStatus.AWAITING_APPROVAL.value:
            raise ValueError(
                f"Run {run_id} is not awaiting approval (status: {run.status})"
            )
        pending = [
            rec
            for rec in run.steps.values()
            if rec.status == StepStatus.AWAITING_APPROVAL.value
        ]
        if not pending:
            raise ValueError(f"Run {run_id} has no pending approval step")
        record = pending[0]
        if not approved:
            record.status = StepStatus.FAILED.value
            record.error = "Rejected by human approver"
            record.log("Approval rejected; run terminated")
            run.status = RunStatus.REJECTED.value
            self._save(run)
            return run
        record.status = StepStatus.COMPLETED.value
        record.output = {"approved": True}
        record.log("Approval granted; resuming run")
        run.context[record.step_id] = record.output
        run.status = RunStatus.RUNNING.value
        self._save(run)
        definition = WorkflowDefinition.from_dict(run.definition)
        self._execute(run, definition)
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.store.get(run_id)

    def list_runs(self, limit: int = 100) -> list[RunRecord]:
        return self.store.list(limit=limit)

    # -- internals ----------------------------------------------------------

    def _require_run(self, run_id: str) -> RunRecord:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(f"No such run: {run_id}")
        return run

    def _save(self, run: RunRecord) -> None:
        import time as _time

        run.updated_at = _time.time()
        self.store.save(run)

    def _execute(self, run: RunRecord, definition: WorkflowDefinition) -> None:
        order = topological_order(definition)
        steps_by_id = {s.id: s for s in definition.steps}
        for step_id in order:
            record = run.steps[step_id]
            if record.status in (
                StepStatus.COMPLETED.value,
                StepStatus.SKIPPED.value,
            ):
                continue  # resuming a paused run: don't redo finished work
            step = steps_by_id[step_id]

            # A FAILED dependency blocks its dependents. A SKIPPED one does
            # not: e.g. routing steps may depend on a human-approval gate
            # that was legitimately skipped for low-priority items.
            failed_dep = [
                d
                for d in step.depends_on
                if run.steps[d].status == StepStatus.FAILED.value
            ]
            if failed_dep:
                record.status = StepStatus.SKIPPED.value
                record.log(f"Skipped: dependency {failed_dep} failed")
                self._save(run)
                continue

            if step.run_if is not None and not self._gate_passes(run, step.run_if):
                record.status = StepStatus.SKIPPED.value
                record.log(f"Skipped: run_if gate {step.run_if} not satisfied")
                self._save(run)
                continue

            if step.type == "human_approval":
                record.status = StepStatus.AWAITING_APPROVAL.value
                record.log(
                    step.config.get(
                        "message", "Waiting for human approval to continue"
                    )
                )
                run.status = RunStatus.AWAITING_APPROVAL.value
                self._save(run)
                return

            self._run_step(run, step, record)
            if record.status == StepStatus.FAILED.value:
                run.status = RunStatus.FAILED.value
                run.error = f"Step '{step_id}' failed: {record.error}"
                # Mark every still-pending downstream step as skipped so the
                # final state shows the full blast radius of the failure.
                # Iterate to a fixpoint so transitive dependents are caught.
                changed = True
                while changed:
                    changed = False
                    for other_id in order:
                        other = run.steps[other_id]
                        if other.status != StepStatus.PENDING.value:
                            continue
                        blocked = any(
                            run.steps[d].status
                            in (StepStatus.FAILED.value, StepStatus.SKIPPED.value)
                            for d in steps_by_id[other_id].depends_on
                        )
                        if blocked:
                            other.status = StepStatus.SKIPPED.value
                            other.log("Skipped: an upstream step failed")
                            changed = True
                self._save(run)
                return
            self._save(run)

        if all(
            rec.status
            in (StepStatus.COMPLETED.value, StepStatus.SKIPPED.value)
            for rec in run.steps.values()
        ):
            run.status = RunStatus.COMPLETED.value
            self._save(run)

    @staticmethod
    def _gate_passes(run: RunRecord, run_if: dict) -> bool:
        ref_step = run_if["step"]
        expected = run_if.get("equals")
        actual = run.context.get(ref_step)
        if isinstance(actual, dict) and "result" in actual:
            actual = actual["result"]
        return str(actual).lower() == str(expected).lower()

    def _run_step(self, run: RunRecord, step, record: StepRecord) -> None:
        executor = self.step_executors.get(step.type)
        if executor is None:
            record.status = StepStatus.FAILED.value
            record.error = f"Unknown step type '{step.type}'"
            return
        record.status = StepStatus.RUNNING.value
        max_attempts = step.retries + 1
        for attempt in range(1, max_attempts + 1):
            record.attempts = attempt
            try:
                output = executor(step, run.context, self.providers)
            except Exception as exc:  # a failing step is data, not a crash
                record.log(f"Attempt {attempt}/{max_attempts} failed: {exc}")
                if attempt < max_attempts and step.retry_delay_seconds:
                    time.sleep(step.retry_delay_seconds)
                continue
            record.output = output
            record.status = StepStatus.COMPLETED.value
            record.log(f"Completed on attempt {attempt}")
            run.context[step.id] = output
            return
        record.status = StepStatus.FAILED.value
        record.error = record.logs[-1] if record.logs else "step failed"
