"""Built-in step executors.

Each executor receives (step definition, run context, providers) and returns
the step's output, which the engine stores at ``context[step_id]``.
Raising an exception marks the attempt as failed (and triggers retries).
"""
from __future__ import annotations

import json

from workflow_engine.models import StepDefinition
from workflow_engine.templating import render, resolve


def execute_transform(step: StepDefinition, context: dict, providers: dict):
    """Build a dict from config.mapping: {output_key: template-or-literal}.

    String values are rendered as templates; non-string values pass through
    as literals. ``config.set`` values are copied verbatim.
    """
    config = step.config
    output: dict = {}
    for key, value in config.get("set", {}).items():
        output[key] = value
    for key, template in config.get("mapping", {}).items():
        output[key] = render(template, context) if isinstance(template, str) else template
    if not output:
        raise ValueError("transform step requires config.mapping or config.set")
    return output


def execute_llm(step: StepDefinition, context: dict, providers: dict):
    """Render config.prompt, call the named provider, store its text.

    With ``config.parse_json: true`` the provider output is parsed as JSON
    and stored as a dict, so later steps can reference its fields.
    """
    provider_name = step.config.get("provider", "mock")
    provider = providers.get(provider_name)
    if provider is None:
        raise ValueError(f"Unknown LLM provider '{provider_name}'")
    prompt = render(step.config["prompt"], context)
    text = provider.generate(prompt)
    if step.config.get("parse_json"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM step returned non-JSON output: {text!r}") from exc
    return text


_OPERATORS = {
    "equals": lambda a, b: a == b,
    "not_equals": lambda a, b: a != b,
    "contains": lambda a, b: str(b) in str(a),
    "greater_than": lambda a, b: float(a) > float(b),
    "less_than": lambda a, b: float(a) < float(b),
    "is_true": lambda a, b: a is True or str(a).lower() == "true",
}


def execute_condition(step: StepDefinition, context: dict, providers: dict):
    """Evaluate {field, operator, value} against the context.

    Output is {"result": bool, "actual": <resolved value>} so branch gates
    (run_if) and later steps can use either form.
    """
    config = step.config
    operator = config.get("operator", "equals")
    if operator not in _OPERATORS:
        raise ValueError(f"Unknown condition operator '{operator}'")
    actual = resolve(context, config["field"])
    expected = config.get("value")
    result = bool(_OPERATORS[operator](actual, expected))
    return {"result": result, "actual": actual}


def execute_human_approval(step: StepDefinition, context: dict, providers: dict):
    """Marker executor — the engine intercepts this type and pauses the run.

    This function is never called during normal execution; it exists so the
    step type is documented in one place.
    """
    raise RuntimeError("human_approval steps are handled by the engine, not executed")


BUILTIN_STEPS = {
    "transform": execute_transform,
    "llm": execute_llm,
    "condition": execute_condition,
    "human_approval": execute_human_approval,
}
