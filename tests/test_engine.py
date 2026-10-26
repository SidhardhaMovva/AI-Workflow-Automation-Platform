"""Engine behaviour tests: ordering, branching, retries, approvals, failure."""
from pathlib import Path

import pytest

from workflow_engine.engine import load_definition_file, topological_order
from workflow_engine.models import WorkflowDefinition
from workflow_engine.providers import MockProvider, OpenAICompatibleProvider, ProviderError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def definition(steps, name="test-wf"):
    return WorkflowDefinition.from_dict({"name": name, "steps": steps})


def test_topological_order_respects_dependencies():
    wf = definition([
        {"id": "c", "type": "transform", "depends_on": ["b"], "config": {"set": {"x": 1}}},
        {"id": "a", "type": "transform", "config": {"set": {"x": 1}}},
        {"id": "b", "type": "transform", "depends_on": ["a"], "config": {"set": {"x": 1}}},
    ])
    assert topological_order(wf) == ["a", "b", "c"]


def test_cycle_is_rejected():
    wf = definition([
        {"id": "a", "type": "transform", "depends_on": ["b"], "config": {"set": {"x": 1}}},
        {"id": "b", "type": "transform", "depends_on": ["a"], "config": {"set": {"x": 1}}},
    ])
    with pytest.raises(ValueError, match="cycle"):
        topological_order(wf)


def test_steps_run_in_dependency_order_and_share_context(engine):
    wf = definition([
        {"id": "second", "type": "transform", "depends_on": ["first"],
         "config": {"mapping": {"combined": "{first.greeting} world"}}},
        {"id": "first", "type": "transform", "config": {"set": {"greeting": "hello"}}},
    ])
    run = engine.start_run(wf)
    assert run.status == "completed"
    assert run.context["second"]["combined"] == "hello world"


def test_condition_branching_with_example_workflow(engine):
    triage = load_definition_file(EXAMPLES / "document_triage.yaml")

    billing = engine.start_run(triage, {"text": "I was charged twice, please refund me"})
    assert billing.status == "completed"
    assert billing.steps["route_billing"].status == "completed"
    assert billing.steps["route_general"].status == "skipped"
    assert billing.context["route_billing"]["queue"] == "billing-team"

    technical = engine.start_run(triage, {"text": "The app shows an error on login"})
    assert technical.status == "completed"
    assert technical.steps["route_general"].status == "completed"
    assert technical.steps["route_billing"].status == "skipped"
    assert technical.context["route_general"]["queue"] == "general-support"


def test_retry_succeeds_after_transient_failures(engine):
    calls = {"n": 0}

    def flaky(step, context, providers):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient boom")
        return {"ok": True}

    engine.register_step_type("flaky", flaky)
    wf = definition([
        {"id": "unstable", "type": "flaky", "retries": 2, "config": {}},
    ])
    run = engine.start_run(wf)
    assert run.status == "completed"
    assert run.steps["unstable"].attempts == 3
    assert run.context["unstable"] == {"ok": True}


def test_retry_exhaustion_fails_run_and_skips_dependents(engine):
    def always_fails(step, context, providers):
        raise RuntimeError("permanent boom")

    engine.register_step_type("always_fails", always_fails)
    wf = definition([
        {"id": "bad", "type": "always_fails", "retries": 1, "config": {}},
        {"id": "after", "type": "transform", "depends_on": ["bad"],
         "config": {"set": {"x": 1}}},
    ])
    run = engine.start_run(wf)
    assert run.status == "failed"
    assert run.steps["bad"].attempts == 2
    assert run.steps["after"].status == "skipped"
    assert "bad" in run.error


def test_failed_step_marks_run_failed(engine):
    wf = definition([
        {"id": "broken", "type": "transform",
         "config": {"mapping": {"x": "{inputs.missing_value}"}}},
    ])
    run = engine.start_run(wf, {"other": 1})
    assert run.status == "failed"
    assert run.steps["broken"].status == "failed"


def test_human_approval_pauses_and_resume_completes(engine):
    triage = load_definition_file(EXAMPLES / "document_triage.yaml")
    run = engine.start_run(
        triage, {"text": "Urgent: I was charged twice and cannot pay rent, refund now"}
    )
    assert run.status == "awaiting_approval"
    assert run.steps["urgent_review"].status == "awaiting_approval"
    assert run.steps["route_billing"].status == "pending"

    resumed = engine.approve(run.id, approved=True)
    assert resumed.status == "completed"
    assert resumed.steps["urgent_review"].status == "completed"
    assert resumed.context["route_billing"]["queue"] == "billing-team"


def test_human_approval_rejection_terminates_run(engine):
    triage = load_definition_file(EXAMPLES / "document_triage.yaml")
    run = engine.start_run(
        triage, {"text": "Urgent billing problem: charged twice, I cannot continue"}
    )
    assert run.status == "awaiting_approval"
    rejected = engine.approve(run.id, approved=False)
    assert rejected.status == "rejected"
    assert rejected.steps["route_billing"].status != "completed"


def test_low_priority_run_skips_approval_and_completes(engine):
    triage = load_definition_file(EXAMPLES / "document_triage.yaml")
    run = engine.start_run(triage, {"text": "Question about my invoice copy"})
    assert run.status == "completed"
    assert run.steps["urgent_review"].status == "skipped"


def test_approval_persists_across_engine_instances(engine, tmp_path):
    from workflow_engine.engine import WorkflowEngine
    from workflow_engine.store import RunStore

    triage = load_definition_file(EXAMPLES / "document_triage.yaml")
    run = engine.start_run(triage, {"text": "Urgent: charged twice, cannot proceed"})
    assert run.status == "awaiting_approval"

    fresh_engine = WorkflowEngine(RunStore(tmp_path / "runs.db"))
    resumed = fresh_engine.approve(run.id, approved=True)
    assert resumed.status == "completed"


def test_summarization_example_end_to_end(engine):
    wf = load_definition_file(EXAMPLES / "content_summarization.yaml")
    run = engine.start_run(wf, {
        "title": "Quarterly update",
        "word_count": 12,
        "text": "Revenue grew this quarter. Costs fell slightly. Hiring continues.",
    })
    assert run.status == "completed"
    package = run.context["package"]
    assert package["title"] == "Quarterly update"
    assert package["status"] == "summarized"
    assert "Revenue grew" in package["summary"]


def test_mock_provider_is_deterministic():
    provider = MockProvider()
    prompt = "Classify and triage this: I need a refund for a double charge"
    assert provider.generate(prompt) == provider.generate(prompt)
    assert '"category": "billing"' in provider.generate(prompt)


def test_openai_provider_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider()
