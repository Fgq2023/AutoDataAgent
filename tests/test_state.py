"""Subtopic B tests: store, manager, provenance, recovery policies."""

from __future__ import annotations

import json

import pytest

from agent.core.types import RecoveryAction, Step, StepStatus, TaskGraph
from agent.state.recovery import classify_message, decide_recovery
from agent.state.store import RunStore

from tests.conftest import TREND_GOAL


@pytest.fixture()
def graph() -> TaskGraph:
    return TaskGraph(
        goal=TREND_GOAL,
        steps=[
            Step("s1", "load", "load_data", {"source": "x.csv", "name": "raw"}),
            Step("s2", "clean", "clean_data", {"input": "raw", "name": "clean"},
                 depends_on=["s1"]),
            Step("s3", "agg", "aggregate",
                 {"input": "clean", "name": "monthly"}, depends_on=["s2"]),
        ],
    )


@pytest.fixture()
def state(outputs_root, graph):
    from agent.state.manager import StateManager

    mgr = StateManager.new_run(outputs_root, TREND_GOAL, graph)
    yield mgr
    mgr.close()


# ---------------------------------------------------------------------- #
# Recovery policy table
# ---------------------------------------------------------------------- #
def test_classify_message():
    assert classify_message("sandbox exceeded 30s timeout") == "timeout"
    assert classify_message("ModuleNotFoundError: no pandas") == "dependency"
    assert classify_message("ValueError: bad args") == "execution"


def test_policy_walkthrough():
    # timeout: RETRY, RETRY, REPLAN, then FAIL
    seq = [decide_recovery("timeout", i) for i in (1, 2, 3)]
    assert seq == [RecoveryAction.RETRY, RecoveryAction.RETRY, RecoveryAction.REPLAN]
    assert decide_recovery("timeout", 4) == RecoveryAction.FAIL
    # execution: RETRY, REPLAN, FAIL
    assert decide_recovery("execution", 2) == RecoveryAction.REPLAN
    assert decide_recovery("execution", 3) == RecoveryAction.FAIL
    # dependency: straight to REPLAN
    assert decide_recovery("dependency", 1) == RecoveryAction.REPLAN
    # verify: RETRY, REPLAN
    assert decide_recovery("verify", 2) == RecoveryAction.REPLAN


# ---------------------------------------------------------------------- #
# Store roundtrip
# ---------------------------------------------------------------------- #
def test_store_step_roundtrip(state):
    state.mark("s1", StepStatus.SUCCEEDED, attempts=1, duration_ms=12)
    rows = {r["step_id"]: r for r in state.step_summaries()}
    assert rows["s1"]["status"] == "succeeded"
    assert rows["s1"]["attempts"] == 1
    assert rows["s2"]["status"] == "pending"


# ---------------------------------------------------------------------- #
# Artifact registry: versioned ids, lookup, invalidation
# ---------------------------------------------------------------------- #
def _register(state, step_id, name, valid=True):
    from agent.core.types import Step
    from agent.tools.base import ArtifactSpec

    step = state.graph.get(step_id)
    spec = ArtifactSpec(name=name, kind="dataframe",
                        path=f"artifacts/{name}.csv", meta={"n_rows": 5})
    (state.run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (state.run_dir / spec.path).write_text("date\n2024-01", encoding="utf-8")
    return state.register_artifacts(step, [spec], [])


def test_artifact_ids_are_versioned_on_re_registration(state):
    a1 = _register(state, "s1", "raw")
    assert a1[0].artifact_id == "raw"
    state.invalidate_step_artifacts("s1")
    a2 = _register(state, "s1", "raw")
    assert a2[0].artifact_id == "raw_v2"       # re-run never shadows history
    assert state.lookup("raw").artifact_id == "raw_v2"
    assert state.store.artifact("raw").valid is False  # but history is auditable


def test_lookup_raises_on_unknown_name(state):
    with pytest.raises(KeyError):
        state.lookup("never_registered")


def test_provenance_chain_is_recorded_and_rendered(state):
    from agent.core.types import Step

    _register(state, "s1", "raw")
    step2 = state.graph.get("s2")
    from agent.tools.base import ArtifactSpec

    spec = ArtifactSpec(name="clean", kind="dataframe",
                        path="artifacts/clean.csv", meta={"n_rows": 4})
    (state.run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (state.run_dir / spec.path).write_text("date\n2024-01", encoding="utf-8")
    state.register_artifacts(step2, [spec], ["raw"])

    lines = state.provenance_lines("clean")
    assert any("[s2] clean_data <- raw" in line for line in lines)
    assert any("[s1] load_data" in line for line in lines)


def test_rollback_resets_downstream_steps_and_artifacts(state):
    _register(state, "s1", "raw")
    _register(state, "s2", "clean")
    state.mark("s1", StepStatus.SUCCEEDED)
    state.mark("s2", StepStatus.SUCCEEDED)

    affected = state.rollback("s1")
    assert affected == ["s2", "s3"]
    rows = {r["step_id"]: r for r in state.step_summaries()}
    assert rows["s2"]["status"] == "pending"
    assert state.lookup("raw")            # upstream artifact still valid
    with pytest.raises(KeyError):
        state.lookup("clean")             # downstream artifact invalidated


# ---------------------------------------------------------------------- #
# Trace: append-only and exported as JSON
# ---------------------------------------------------------------------- #
def test_trace_export(state, outputs_root):
    state.trace("custom_event", {"k": "v"})
    path = state.export_trace()
    events = json.loads(path.read_text(encoding="utf-8"))
    kinds = [e["event"] for e in events]
    assert "custom_event" in kinds
    assert events[-1]["payload"]["k"] == "v"


def test_run_dir_layout(state):
    assert (state.run_dir / "state.db").is_file()
    assert (state.run_dir / "artifacts").is_dir()
    assert state.run_id.startswith("run_")
