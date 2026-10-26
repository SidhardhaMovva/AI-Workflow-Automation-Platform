"""Data model for workflow definitions and run state."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass
class StepDefinition:
    id: str
    type: str  # transform | llm | condition | human_approval | custom registered types
    config: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    # Branch gate: run this step only if the referenced step's output
    # equals ``value`` (compared as strings, so YAML true/false works).
    run_if: dict | None = None  # {"step": <step_id>, "equals": <value>}
    retries: int = 0
    retry_delay_seconds: float = 0.0

    @staticmethod
    def from_dict(raw: dict) -> "StepDefinition":
        return StepDefinition(
            id=raw["id"],
            type=raw["type"],
            config=raw.get("config", {}),
            depends_on=raw.get("depends_on", []),
            run_if=raw.get("run_if"),
            retries=int(raw.get("retries", 0)),
            retry_delay_seconds=float(raw.get("retry_delay_seconds", 0.0)),
        )


@dataclass
class WorkflowDefinition:
    name: str
    steps: list[StepDefinition]
    description: str = ""

    @staticmethod
    def from_dict(raw: dict) -> "WorkflowDefinition":
        steps = [StepDefinition.from_dict(s) for s in raw["steps"]]
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate step ids in workflow '{raw.get('name')}'")
        known = set(ids)
        for step in steps:
            for dep in step.depends_on:
                if dep not in known:
                    raise ValueError(
                        f"Step '{step.id}' depends on unknown step '{dep}'"
                    )
        return WorkflowDefinition(
            name=raw["name"],
            description=raw.get("description", ""),
            steps=steps,
        )


@dataclass
class StepRecord:
    step_id: str
    type: str
    status: str = StepStatus.PENDING.value
    attempts: int = 0
    output: object = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")


@dataclass
class RunRecord:
    workflow_name: str
    definition: dict
    inputs: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = RunStatus.PENDING.value
    context: dict = field(default_factory=dict)
    steps: dict[str, StepRecord] = field(default_factory=dict)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @staticmethod
    def from_dict(data: dict) -> "RunRecord":
        steps = {
            sid: StepRecord(**srec) for sid, srec in data.get("steps", {}).items()
        }
        return RunRecord(
            workflow_name=data["workflow_name"],
            definition=data["definition"],
            inputs=data.get("inputs", {}),
            id=data["id"],
            status=data.get("status", RunStatus.PENDING.value),
            context=data.get("context", {}),
            steps=steps,
            error=data.get("error"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
