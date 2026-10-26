"""Tiny dotted-path resolution and ``{placeholder}`` template rendering.

Workflow configs reference values produced earlier in a run, e.g.
``{inputs.text}`` or ``{classify.category}``. Resolution walks dicts in the
run context; a missing path is an error, never a silent empty string.
"""
from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.]+)\}")


def resolve(context: dict, path: str):
    parts = path.split(".")
    node = context
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise KeyError(f"Cannot resolve '{path}': '{part}' not found")
    return node


def render(template: str, context: dict) -> str:
    def _replace(match: re.Match) -> str:
        return str(resolve(context, match.group(1)))

    return _PLACEHOLDER.sub(_replace, template)
