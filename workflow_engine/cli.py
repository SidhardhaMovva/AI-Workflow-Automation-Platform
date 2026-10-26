"""Command line interface.

Examples:
    python -m workflow_engine.cli run examples/document_triage.yaml \\
        --input text="I was charged twice and need a refund, this is urgent"
    python -m workflow_engine.cli approve <run_id>
    python -m workflow_engine.cli list
    python -m workflow_engine.cli show <run_id>
"""
from __future__ import annotations

import argparse
import json

from workflow_engine.engine import WorkflowEngine, load_definition_file
from workflow_engine.store import RunStore


def _parse_inputs(pairs: list[str]) -> dict:
    inputs = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        inputs[key] = value
    return inputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workflow_engine")
    parser.add_argument("--db", default="workflow_runs.db", help="SQLite run store")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run a workflow definition file")
    p_run.add_argument("file")
    p_run.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")

    p_approve = sub.add_parser("approve", help="Approve (or reject) a paused run")
    p_approve.add_argument("run_id")
    p_approve.add_argument("--reject", action="store_true")

    sub.add_parser("list", help="List recent runs")

    p_show = sub.add_parser("show", help="Show one run")
    p_show.add_argument("run_id")

    args = parser.parse_args(argv)
    engine = WorkflowEngine(RunStore(args.db))

    if args.command == "run":
        definition = load_definition_file(args.file)
        run = engine.start_run(definition, _parse_inputs(args.input))
        print(json.dumps(run.to_dict(), indent=2))
        return 0 if run.status in ("completed", "awaiting_approval") else 1
    if args.command == "approve":
        run = engine.approve(args.run_id, approved=not args.reject)
        print(json.dumps(run.to_dict(), indent=2))
        return 0 if run.status in ("completed", "awaiting_approval", "rejected") else 1
    if args.command == "list":
        for run in engine.list_runs():
            print(f"{run.id}  {run.status:<18}  {run.workflow_name}")
        return 0
    if args.command == "show":
        run = engine.get_run(args.run_id)
        if run is None:
            print(f"No such run: {args.run_id}")
            return 1
        print(json.dumps(run.to_dict(), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
