"""LLM providers.

``MockProvider`` is the default: deterministic, rule-based, and runs with no
API keys and no network, which is what makes the example workflows and the
test suite reproducible.

``OpenAICompatibleProvider`` is optional. It talks to any OpenAI-compatible
``/chat/completions`` endpoint using only the standard library, and only
when an API key is supplied via the environment.
"""
from __future__ import annotations

import json
import os
import urllib.request


class ProviderError(RuntimeError):
    pass


class MockProvider:
    """Deterministic stand-in for an LLM.

    Same prompt in -> same text out, every time. The rules are deliberately
    simple and visible; this is a test double with behaviour, not a model.
    """

    name = "mock"

    def generate(self, prompt: str) -> str:
        text = prompt.strip()
        lowered = text.lower()
        if "classify" in lowered or "triage" in lowered or "categorize" in lowered:
            return self._classify(text)
        if "summarize" in lowered or "summary" in lowered:
            return self._summarize(text)
        # Generic fallback: echo a trimmed, stable response.
        snippet = " ".join(text.split())[:120]
        return f"Mock response: {snippet}"

    @staticmethod
    def _classify(text: str) -> str:
        lowered = text.lower()
        if any(w in lowered for w in ("refund", "charge", "invoice", "billing", "payment")):
            category = "billing"
        elif any(w in lowered for w in ("login", "password", "error", "crash", "bug")):
            category = "technical"
        elif any(w in lowered for w in ("cancel", "subscription", "plan")):
            category = "account"
        else:
            category = "general"
        priority = (
            "high"
            if any(w in lowered for w in ("urgent", "down", "cannot", "can't", "blocked"))
            else "normal"
        )
        return json.dumps({"category": category, "priority": priority})

    @staticmethod
    def _summarize(text: str) -> str:
        # Drop the instruction if the prompt starts with one, either as its
        # own line or as a "Summarize ...: <body>" prefix on a single line.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        body = lines[-1] if lines else ""
        prefix, sep, rest = body.partition(": ")
        if sep and ("summar" in prefix.lower()) and len(prefix) < 80:
            body = rest
        sentences = [s.strip() for s in body.replace("\n", " ").split(".") if s.strip()]
        summary = ". ".join(sentences[:2])
        if summary and not summary.endswith("."):
            summary += "."
        return f"Summary: {summary}"


class OpenAICompatibleProvider:
    """Optional provider for OpenAI-compatible chat completions endpoints.

    Requires ``OPENAI_API_KEY`` (or a key passed in). Never used implicitly:
    a workflow must name it, and constructing it without a key raises, so a
    run can never silently start spending money or calling the network.
    """

    name = "openai-compatible"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ProviderError(
                "OpenAICompatibleProvider requires OPENAI_API_KEY to be set."
            )

    def generate(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            }
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode())
        except Exception as exc:  # network / HTTP errors surface as run failures
            raise ProviderError(f"Provider request failed: {exc}") from exc
        return data["choices"][0]["message"]["content"]
