import io
import json
import urllib.error
from pathlib import Path

import pytest

from agent.llm.client import LLMClient, LLMProviderStop
from agent.planning.llm_planner import LLMPlanner
from agent.planning.replanner import FeedbackReplanner
from tests.conftest import TREND_GOAL


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_initial_provider_failure_is_durable_and_stops(orchestrator, monkeypatch, status):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-test-value")
    client = LLMClient()

    def fail(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, status, "rejected", {}, io.BytesIO(b"secret-test-value"))

    monkeypatch.setattr("urllib.request.urlopen", fail)
    orchestrator.planner = LLMPlanner(client)
    with pytest.raises(LLMProviderStop):
        orchestrator.run(TREND_GOAL)
    run = next(Path(orchestrator.config.outputs_root).iterdir())
    raw = (run / "trace.json").read_text(encoding="utf-8")
    events = json.loads(raw)
    assert "secret-test-value" not in raw
    assert sum(e["event"] == "model_request_started" for e in events) == 1
    assert any(e["event"] == "model_request_failed" and e["payload"]["status"] == status for e in events)
    assert any(e["event"] == "run_stopped" for e in events)
    assert not any(e["event"] == "step_attempt" for e in events)


def test_initial_transport_retry_history_persists(orchestrator, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client = LLMClient()
    calls = 0

    def response(request, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError("temporary disconnect")
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                     "usage": {"total_tokens": 3}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", response)
    monkeypatch.setattr("time.sleep", lambda delay: None)
    original = orchestrator.planner.plan

    class Planner:
        def __init__(self):
            self.client = client

        def plan(self, goal, ctx):
            client.complete("system", "user")
            return original(goal, ctx)

    orchestrator.planner = Planner()
    result = orchestrator.run(TREND_GOAL)
    events = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    selected = [e["event"] for e in events if e["event"].startswith("model_")]
    assert selected == ["model_request_started", "model_request_failed", "model_transport_retry",
                        "model_request_started", "model_request_succeeded"]
    from agent.state.store import RunStore
    from agent.state.journal import load
    store = RunStore(Path(result.run_dir) / "state.db")
    assert load(store)["model_requests"] == 2
    store.close()


def test_repair_quota_error_stops_whole_run(orchestrator, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client = LLMClient()

    def fail(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 402, "quota", {}, io.BytesIO(b"quota exhausted"))

    monkeypatch.setattr("urllib.request.urlopen", fail)
    original = orchestrator.planner.plan

    def plan(goal, ctx):
        graph = original(goal, ctx)
        graph.get("s4").args["x"] = "missing"
        return graph

    orchestrator.planner.plan = plan
    orchestrator.replanner = FeedbackReplanner(client)
    with pytest.raises(LLMProviderStop):
        orchestrator.run(TREND_GOAL)
    run = next(Path(orchestrator.config.outputs_root).iterdir())
    events = json.loads((run / "trace.json").read_text(encoding="utf-8"))
    assert any(e["event"] == "run_stopped" and e["payload"]["reason"] == "model_quota_or_rate_limit" for e in events)
    assert not any(e["event"] == "step_attempt" and e["payload"]["step_id"] == "s5" for e in events)


def test_cli_reports_provider_stop_and_trace_location(monkeypatch, data_root, outputs_root, capsys):
    from agent.cli import main
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    def fail(*args):
        raise LLMProviderStop(402, "quota exhausted")

    monkeypatch.setattr(LLMPlanner, "_ask", fail)
    code = main([TREND_GOAL, "--mode", "llm", "--data-root", str(data_root),
                 "--outputs-root", str(outputs_root)])
    assert code == 2
    error = capsys.readouterr().err
    assert "quota exhausted" in error and "[run]" in error
    assert len(list(outputs_root.glob("*/trace.json"))) == 1
