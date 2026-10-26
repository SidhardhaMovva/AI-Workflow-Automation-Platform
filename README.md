# AI Workflow Automation Platform

A small DAG workflow engine in Python for pipelines that mix deterministic
steps, LLM steps, branching, and human approval — with retries and persisted
run state, so a run paused for approval can be resumed later, even by a
different process.

Workflows are defined in YAML (or JSON). The engine runs them with the
built-in **mock LLM provider** by default: deterministic, no API keys, no
network. An OpenAI-compatible provider is included as an option and is never
used unless a workflow explicitly names it and a key is configured.

## Concepts

| Step type | What it does |
|---|---|
| `transform` | Builds a dict from templates/literals, e.g. `queue: "{classify.category}"` |
| `llm` | Renders a prompt, calls a provider, stores the text (or parses it as JSON with `parse_json: true`) |
| `condition` | Evaluates `field / operator / value` against the run context → `{"result": bool, "actual": …}` |
| `human_approval` | Pauses the run, persists it as `awaiting_approval`, and waits for `approve` |

Steps declare `depends_on` (execution order is a topological sort) and can
declare a `run_if` gate — `run_if: {step: is_billing, equals: true}` — to
implement branching. Any step can set `retries` (with optional
`retry_delay_seconds`). A failed step fails the run and marks its downstream
steps `skipped`; the full per-step state (status, attempts, logs, output,
error) is stored in SQLite after every transition.

Custom step types and providers can be registered in code
(`engine.register_step_type(...)`, `engine.register_provider(...)`) — the
test suite uses this to exercise retry behaviour with a deliberately flaky
step.

## Defining a workflow

```yaml
name: document-triage
steps:
  - id: classify
    type: llm
    config:
      provider: mock
      parse_json: true
      prompt: "Classify and triage this support message: {inputs.text}"

  - id: is_billing
    type: condition
    depends_on: [classify]
    config: {field: classify.category, operator: equals, value: billing}

  - id: route_billing
    type: transform
    depends_on: [is_billing]
    run_if: {step: is_billing, equals: true}
    config:
      mapping: {queue: "billing-team", category: "{classify.category}"}
```

`{dotted.path}` placeholders resolve against the run context: `inputs.*`
for run inputs and `<step_id>.<field>` for earlier step outputs. A missing
path fails the step — templates never silently render empty values.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# CLI — summarization example
python -m workflow_engine.cli run examples/content_summarization.yaml \
  --input title="Quarterly update" --input word_count=12 \
  --input text="Revenue grew this quarter. Costs fell slightly."

# CLI — triage example: high-priority items pause for approval
python -m workflow_engine.cli run examples/document_triage.yaml \
  --input text="Urgent: I was charged twice and cannot pay rent, refund now"
# → status: awaiting_approval, run id printed in the JSON output
python -m workflow_engine.cli approve <run_id>        # or: approve <run_id> --reject
python -m workflow_engine.cli list

# API
uvicorn workflow_engine.api:app --port 8000
curl -s localhost:8000/examples
curl -s -X POST localhost:8000/examples/document_triage/run \
  -H 'Content-Type: application/json' \
  -d '{"inputs": {"text": "The app shows an error on login"}}'
curl -s -X POST localhost:8000/runs/<run_id>/approve \
  -H 'Content-Type: application/json' -d '{"approved": true}'
```

Two example workflows are included and run end-to-end with the mock
provider: `examples/document_triage.yaml` (classify → condition branches →
approval gate for high-priority items → routing) and
`examples/content_summarization.yaml` (prepare → summarize → package).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

20 tests cover: topological ordering and cycle detection, context sharing
between steps, condition branching (both branches of the triage example),
retry success after transient failures, retry exhaustion failing the run and
skipping dependents, failed-step handling, approval pause/resume, approval
rejection, resuming a paused run from a fresh engine instance against the
same SQLite store, the summarization example end-to-end, mock-provider
determinism, the OpenAI provider refusing to start without a key, and the
HTTP API (run/list/approve, 404/422 handling, runnable examples).

## Layout

```
workflow_engine/
  models.py      workflow/step/run dataclasses and statuses
  engine.py      DAG ordering, execution, retries, approvals
  steps.py       built-in step executors
  providers.py   MockProvider (default) + optional OpenAI-compatible provider
  store.py       SQLite persistence for runs
  templating.py  dotted-path resolution and {placeholder} rendering
  api.py         FastAPI layer
  cli.py         command line interface
examples/        two runnable workflows
tests/           engine + API test suites
```

## Limitations (honest ones)

- Execution is synchronous and in-process: no worker queue, no parallelism
  between independent steps, no scheduling. Long-running LLM calls block
  the caller.
- The mock provider is a deterministic rule-based test double. It behaves
  consistently enough to develop and test workflows against, but it is not
  a language model and its outputs are only as smart as its keyword rules.
- The OpenAI-compatible provider is a thin standard-library client with no
  streaming, no token accounting, and no retry of its own (use step-level
  `retries`).
- Run state is a single SQLite file — fine for local/single-node use, not
  a distributed store. No authentication on the API.

## License

MIT — see `LICENSE`.
