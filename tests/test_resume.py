import json
from pathlib import Path
import pytest
from tests.conftest import TREND_GOAL


def test_resume_reuses_verified_steps_and_preserves_budget(orchestrator):
    count = 0

    def cancel():
        nonlocal count
        count += 1
        return count > 2

    first = orchestrator.run(TREND_GOAL, cancelled=cancel)
    second = orchestrator.resume(first.run_dir)
    assert second.status == "finished"
    trace = json.loads(Path(second.trace_path).read_text(encoding="utf-8"))
    assert sum(e["event"] == "step_attempt" and e["payload"]["step_id"] == "s1" for e in trace) == 1
    assert any(e["event"] == "run_resumed" for e in trace)


def test_resume_refuses_changed_input(orchestrator, data_root):
    first = orchestrator.run(TREND_GOAL, cancelled=lambda: True)
    with (data_root / "sales.csv").open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="input changed"):
        orchestrator.resume(first.run_dir)


def test_hard_exit_resume_reuses_two_upstream_steps(tmp_path):
    import os
    import subprocess
    import sys
    from agent.core.config import AgentConfig
    from agent.core.orchestrator import Orchestrator
    from agent.planning.mock_planner import MockPlanner
    from agent.planning.replanner import Replanner
    from agent.state.manager import StateManager
    from agent.tools import REGISTRY

    root = tmp_path / "case"
    script = """
import os, sys
from pathlib import Path
from examples.demo_cross_source import prepare, CrossSourcePlanner
from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.planning.replanner import Replanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY
root = Path(sys.argv[1])
spec = prepare(root)
agent = Orchestrator(CrossSourcePlanner(), Replanner(), REGISTRY, StateManager.new_run,
                     AgentConfig(data_root=str(root), outputs_root=str(root / "runs")))
original = agent._attempt
def crash(step, *args):
    if step.step_id == "join":
        os._exit(17)
    return original(step, *args)
agent._attempt = crash
agent.run(spec)
"""
    proc = subprocess.run([sys.executable, "-c", script, str(root)], timeout=30)
    assert proc.returncode == 17
    run = next((root / "runs").iterdir())
    agent = Orchestrator(MockPlanner(), Replanner(), REGISTRY, StateManager.new_run, AgentConfig())
    result = agent.resume(run)
    assert result.status == "finished"
    trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    for sid in ("sales", "customers"):
        assert sum(e["event"] == "step_attempt" and e["payload"]["step_id"] == sid for e in trace) == 1
    assert sum(e["event"] == "step_attempt" and e["payload"]["step_id"] == "join" for e in trace) == 2


def test_resume_refuses_modified_verified_artifact(orchestrator):
    result = orchestrator.run(TREND_GOAL)
    art = next(a for a in result.artifacts if a["valid"])
    (Path(result.run_dir) / art["path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact changed"):
        orchestrator.resume(result.run_dir)


def test_resume_does_not_reset_tool_budget(orchestrator):
    orchestrator.config.max_tool_attempts = 1
    first = orchestrator.run(TREND_GOAL)
    second = orchestrator.resume(first.run_dir)
    assert second.status == "finished_with_failures"
    trace = json.loads(Path(second.trace_path).read_text(encoding="utf-8"))
    assert sum(e["event"] == "step_attempt" for e in trace) == 1


def test_resume_rejects_manifest_changes(orchestrator):
    import sqlite3
    result = orchestrator.run(TREND_GOAL, cancelled=lambda: True)
    with sqlite3.connect(Path(result.run_dir) / "state.db") as db:
        db.execute("UPDATE resume_manifest SET payload='{}'")
    with pytest.raises(ValueError, match="integrity"):
        orchestrator.resume(result.run_dir)


def test_resume_refuses_concurrent_owner(orchestrator):
    from agent.state.lock import run_lock
    result = orchestrator.run(TREND_GOAL, cancelled=lambda: True)
    with run_lock(result.run_dir):
        with pytest.raises(ValueError, match="already being resumed"):
            orchestrator.resume(result.run_dir)


def test_resume_keeps_elapsed_time(orchestrator):
    import sqlite3
    from agent.state import journal
    from agent.state.manager import StateManager
    from agent.state.store import RunStore

    first = orchestrator.run(TREND_GOAL, cancelled=lambda: True)
    store = RunStore(Path(first.run_dir) / "state.db")
    manifest = journal.load(store)
    manifest["elapsed"] = manifest["config"]["run_timeout_s"] + 1
    state = type("State", (), {"store": store})()
    journal.save(state, manifest)
    store.close()
    result = orchestrator.resume(first.run_dir)
    trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert not any(e["event"] == "step_attempt" for e in trace)
    assert any(e["event"] == "run_stopped" and e["payload"]["reason"] == "run_timeout" for e in trace)


def test_resume_keeps_model_request_budget(orchestrator):
    from agent.planning.replanner import FeedbackReplanner
    from agent.state import journal
    from agent.state.store import RunStore

    # Simulate a saved run that has consumed all model requests.
    first = orchestrator.run(TREND_GOAL, cancelled=lambda: True)
    store = RunStore(Path(first.run_dir) / "state.db")
    manifest = journal.load(store)
    manifest["model_requests"] = 8
    state = type("State", (), {"store": store})()
    journal.save(state, manifest)
    store.close()

    class Client:
        before_request = None
        calls = 0

        def complete(self, *args, **kwargs):
            self.before_request()
            self.calls += 1
            raise AssertionError("provider should never be contacted")

    client = Client()
    orchestrator.replanner = FeedbackReplanner(client)
    # Force a repair of the unchanged persisted graph.
    original_attempt = orchestrator._attempt

    def wrong_column(step, *args):
        if step.tool == "chart":
            step.args["x"] = "missing"
        return original_attempt(step, *args)

    orchestrator._attempt = wrong_column
    result = orchestrator.resume(first.run_dir)
    assert result.status == "finished_with_failures"
    assert client.calls == 0
    store = RunStore(Path(first.run_dir) / "state.db")
    assert journal.load(store)["model_requests"] == 8
    store.close()


def test_cli_resume_requires_saved_live_mode(orchestrator, monkeypatch, capsys):
    from agent.cli import main
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    orchestrator.config.mode = "llm"
    result = orchestrator.run(TREND_GOAL, cancelled=lambda: True)
    assert main(["--resume", result.run_dir, "--mode", "mock"]) == 2
    assert "requires live model credentials" in capsys.readouterr().err


def test_resume_restores_nonsecret_model_identity(orchestrator):
    class Client:
        provider = "openai"
        base_url = "https://example.test/go/v1"
        model = "original-model"
        session_id = "original-session"
        request_count = 0

    client = Client()
    orchestrator.planner.client = client
    first = orchestrator.run(TREND_GOAL, cancelled=lambda: True)
    client.base_url = "https://different.test/v1"
    client.model = "different-model"
    client.session_id = "different-session"
    result = orchestrator.resume(first.run_dir)
    assert result.status == "finished"
    assert client.base_url == "https://example.test/go/v1"
    assert client.model == "original-model"
    assert client.session_id == "original-session"
