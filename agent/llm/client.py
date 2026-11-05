"""A tiny OpenAI-compatible chat client (stdlib only).

Two providers:

  * ``openai`` — any OpenAI-compatible endpoint (``OPENAI_BASE_URL``
    supported for gateways / regional deployments)
  * ``mock``   — no network, deterministic; the system runs fully offline

Live planning requires model credentials. Offline demos explicitly select mock mode.
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


class LLMProviderStop(LLMError):
    """Authentication, quota or rate-limit failure requiring a run stop."""

    def __init__(self, status, message):
        self.status = status
        self.reason = "model_authentication" if status in (401, 403) else "model_quota_or_rate_limit"
        super().__init__(message)


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
        self.request_count = 0
        self.before_request = None
        self.on_event = None

    # ------------------------------------------------------------------ #
    @property
    def is_live(self) -> bool:
        """True when a real provider is configured."""
        return self.provider == "openai" and bool(self.api_key)

    def emit(self, event, **payload):
        if self.on_event:
            self.on_event(event, payload)

    def reserve_request(self):
        timeout = self.before_request() if self.before_request else None
        self.request_count += 1
        return timeout

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
        for attempt in range(2):
            request_timeout = self.reserve_request() or 60
            attempt_started = time.perf_counter()
            self.emit("model_request_started", request=self.request_count, attempt=attempt + 1, model=self.model)
            try:
                with urllib.request.urlopen(req, timeout=min(60, request_timeout)) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                self.emit("model_request_succeeded", request=self.request_count, usage=body.get("usage"),
                          duration_ms=round((time.perf_counter() - attempt_started) * 1000))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                detail = detail.replace(self.api_key, "[REDACTED]")[:500]
                self.emit("model_request_failed", request=self.request_count, status=exc.code,
                          reason="http_error", detail=detail)
                error = LLMProviderStop if exc.code in (401, 402, 403, 429) else None
                if error:
                    raise error(exc.code, f"LLM HTTP {exc.code}: {detail}") from exc
                raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                detail = str(exc).replace(self.api_key, "[REDACTED]")
                self.emit("model_request_failed", request=self.request_count, reason="transport_error",
                          detail=detail)
                if attempt == 0:
                    delay = min(1, max(0, request_timeout - (time.perf_counter() - attempt_started)))
                    self.emit("model_transport_retry", request=self.request_count, delay_s=delay,
                              next_attempt=2, reason=detail)
                    time.sleep(delay)
                    continue
                raise LLMError(f"LLM unreachable: {detail}") from exc

        try:
            self.last_call = {"model": body.get("model", self.model),
                              "usage": body.get("usage"),
                              "duration_ms": round((time.perf_counter() - started) * 1000),
                              "finish_reason": body["choices"][0].get("finish_reason")}
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {body}") from exc
