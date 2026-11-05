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


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_normal_planner_propagates_provider_stop(monkeypatch, data_root, status):
    from agent.core.context import profile_data_sources
    from agent.llm.client import LLMClient, LLMError
    from agent.planning.llm_planner import LLMPlanner

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client = LLMClient()
    planner = LLMPlanner(client)
    def fail(*args):
        raise LLMError(f"HTTP {status}")
    monkeypatch.setattr(planner, "_ask", fail)
    with pytest.raises(LLMError):
        planner.plan("Compute totals", profile_data_sources(data_root))
