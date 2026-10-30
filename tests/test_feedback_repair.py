import json
from pathlib import Path

import pytest

from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import FeedbackReplanner
from tests.conftest import TREND_GOAL


class BadChartPlanner(MockPlanner):
    def plan(self, goal, ctx):
        graph = super().plan(goal, ctx)
        graph.get("s4").args["y"] = "reveneu"
        return graph


class RepairClient:
    def complete(self, system, user):
        payload = json.loads(user)
        assert "reveneu" in payload["feedback"]["error"]
        assert "revenue" in [c["name"] for c in payload["inputs"][0]["meta"]["columns"]]
        args = payload["step"]["args"]
        args["y"] = "revenue"
        return json.dumps({"args": args})


def test_feedback_repairs_failed_chart_without_reexecuting_upstream(orchestrator):
    orchestrator.planner = BadChartPlanner()
    orchestrator.replanner = FeedbackReplanner(RepairClient())
    summary = orchestrator.run(TREND_GOAL)
    assert summary.status == "finished"
    assert summary.n_recovered == 1
    events = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
    attempts = [e["payload"]["step_id"] for e in events if e["event"] == "step_attempt"]
    assert attempts == ["s1", "s2", "s3", "s4", "s4", "s4", "s5"]
    assert any(e["event"] == "parameter_repair" for e in events)
    assert Path(summary.report_path).is_file()


def test_permanent_failure_on_both_aggregate_tools_is_bounded(orchestrator, monkeypatch):
    from agent.tools.builtins.aggregate import AggregateTool, AggregatePivotTool

    def fail(*args):
        raise ValueError("persistent failure")

    monkeypatch.setattr(AggregateTool, "run", fail)
    monkeypatch.setattr(AggregatePivotTool, "run", fail)
    summary = orchestrator.run(TREND_GOAL)
    assert summary.status == "finished_with_failures"
    events = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
    assert len([e for e in events if e["event"] == "step_attempt" and e["payload"]["step_id"] == "s3"]) == 3
    assert any(e["event"] == "budget_exhausted" for e in events)


def test_zero_replan_budget_does_not_call_model(orchestrator):
    class NeverClient:
        def complete(self, *args):
            pytest.fail("model called with zero budget")

    orchestrator.config.max_replans = 0
    orchestrator.planner = BadChartPlanner()
    orchestrator.replanner = FeedbackReplanner(NeverClient())
    summary = orchestrator.run(TREND_GOAL)
    assert summary.n_failed == 1 and summary.n_skipped == 1


def test_rejected_repair_preserves_artifact_references(orchestrator):
    class WrongClient(RepairClient):
        def complete(self, system, user):
            args = json.loads(super().complete(system, user))["args"]
            args["input"] = "sales_raw"
            return json.dumps({"args": args})

    orchestrator.planner = BadChartPlanner()
    orchestrator.replanner = FeedbackReplanner(WrongClient())
    summary = orchestrator.run(TREND_GOAL)
    assert summary.n_failed == 1
    events = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
    assert any(e["event"] == "repair_rejected" for e in events)


@pytest.mark.parametrize("response", ["not JSON", '{"args": null}', "unknown_column"])
def test_invalid_model_repair_fails_closed(orchestrator, response):
    class InvalidClient:
        def complete(self, system, user):
            if response != "unknown_column":
                return response
            args = json.loads(user)["step"]["args"]
            args["y"] = "another_missing_column"
            return json.dumps({"args": args})

    orchestrator.planner = BadChartPlanner()
    orchestrator.replanner = FeedbackReplanner(InvalidClient())
    summary = orchestrator.run(TREND_GOAL)
    assert summary.n_failed == 1 and summary.report_path is None
    events = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
    assert any(e["event"] == "repair_rejected" for e in events)
    assert not any(e["event"] == "parameter_repair" for e in events)


def test_aggregation_does_not_silently_drop_invalid_requested_column(orchestrator):
    class PartialColumnsPlanner(MockPlanner):
        def plan(self, goal, ctx):
            graph = super().plan(goal, ctx)
            graph.get("s3").args["value_columns"] = ["units", "reveneu"]
            return graph

    orchestrator.planner = PartialColumnsPlanner()
    summary = orchestrator.run(TREND_GOAL)
    events = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
    errors = [e["payload"] for e in events if e["event"] == "step_attempt_failed"]
    assert any(e["step_id"] == "s3" and "reveneu" in e["error"] for e in errors)
    assert summary.n_failed == 1
