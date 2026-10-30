"""A tiny OpenAI-compatible chat client (stdlib only).

Two providers:

  * ``openai`` — any OpenAI-compatible endpoint (``OPENAI_BASE_URL``
    supported for gateways / regional deployments)
  * ``mock``   — no network, deterministic; the system runs fully offline

The planner degrades gracefully: without ``OPENAI_API_KEY`` the LLM
planner falls back to the deterministic mock planner (see
:mod:`agent.planning.llm_planner`).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
import time


class LLMError(Exception):
    """Raised when the provider call fails."""


class LLMClient:
    def __init__(self, provider: str | None = None):
        if provider is None:
            provider = "openai" if os.environ.get("OPENAI_API_KEY") else "mock"
        self.provider = provider
        self.base_url = (
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        )
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        # Stable for this client/conversation, as required by OpenCode Go.
        self.session_id = uuid.uuid4().hex
        self.last_call = None

    # ------------------------------------------------------------------ #
    @property
    def is_live(self) -> bool:
        """True when a real provider is configured."""
        return self.provider == "openai" and bool(self.api_key)

    def complete(self, system: str, user: str, max_tokens: int = 4000,
                 temperature: float = 0.0) -> str:
        if not self.is_live:
            raise LLMError(
                f"provider {self.provider!r} is not a live LLM; "
                "set OPENAI_API_KEY (or use the mock planner)"
            )

        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")

        req = urllib.request.Request(  # noqa: S310 — fixed scheme/URL from env
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "AutoDataAgent/0.1.0",
                "Accept": "application/json",
                "x-opencode-session": self.session_id,
            },
            method="POST",
        )
        started = time.perf_counter()
        self.last_call = None
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            detail = detail.replace(self.api_key, "[REDACTED]")[:500]
            raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM unreachable: {exc.reason}") from exc

        try:
            self.last_call = {"model": body.get("model", self.model),
                              "usage": body.get("usage"),
                              "duration_ms": round((time.perf_counter() - started) * 1000),
                              "finish_reason": body["choices"][0].get("finish_reason")}
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {body}") from exc
