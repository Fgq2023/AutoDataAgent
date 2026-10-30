"""Live validation must fail closed, never turn an API failure into mock success."""
from pathlib import Path

import pytest


def test_live_planner_does_not_fallback(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    from run_live_validation import RecordingClient, StrictPlanner
    from agent.llm.client import LLMError

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    planner = StrictPlanner(client=RecordingClient(tmp_path))

    def fail(*args):
        raise LLMError("HTTP 402")

    monkeypatch.setattr(planner, "_ask", fail)
    monkeypatch.setattr(planner.fallback, "plan", lambda *args: pytest.fail("mock fallback used"))
    with pytest.raises(LLMError, match="402"):
        planner.plan("demo", None)


def test_live_planner_rejects_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from run_live_validation import RecordingClient, StrictPlanner

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="live model"):
        StrictPlanner(client=RecordingClient(tmp_path)).plan("demo", None)
