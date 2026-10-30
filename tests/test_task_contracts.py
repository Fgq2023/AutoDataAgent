import json
from pathlib import Path

import pytest

from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import FeedbackReplanner
from tests.conftest import SQL_GOAL


class WrongYearPlanner(MockPlanner):
    def plan(self, goal, ctx):
        graph = super().plan(goal, ctx)
        graph.get("s1").args["query"] = graph.get("s1").args["query"].replace("2024", "2025")
        return graph


def contract(data_root):
    return {"kind": "customer_totals_year", "artifact": "customer_totals", "year": 2024, "source": str(data_root / "orders.db")}


def test_wrong_year_passes_old_checks(orchestrator):
    orchestrator.planner = WrongYearPlanner()
    assert orchestrator.run(SQL_GOAL).status == "finished"


def test_contract_detects_wrong_year_and_invalidates_outputs(orchestrator, data_root):
    orchestrator.planner = WrongYearPlanner()
    orchestrator.config.task_contracts = [contract(data_root)]
    result = orchestrator.run(SQL_GOAL)
    assert result.n_failed == 1 and result.report_path is None
    assert all(not a["valid"] for a in result.artifacts)
    events = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    feedback = next(e["payload"] for e in events if e["event"] == "failure_feedback")
    assert feedback["classification"] == "verify"
    assert any(c["name"].startswith("task:") for c in feedback["verdict"]["checks"])


def test_contract_repairs_year_and_keeps_invalid_attempts_auditable(orchestrator, data_root):
    class Client:
        def complete(self, system, user):
            request = json.loads(user)
            assert "year=2024" in request["feedback"]["error"]
            args = request["step"]["args"]
            args["query"] = args["query"].replace("2025", "2024")
            return json.dumps({"args": args})

    orchestrator.planner = WrongYearPlanner()
    orchestrator.config.task_contracts = [contract(data_root)]
    orchestrator.replanner = FeedbackReplanner(Client())
    result = orchestrator.run(SQL_GOAL)
    assert result.status == "finished" and result.n_recovered == 1
    assert len([a for a in result.artifacts if not a["valid"]]) == 2
    assert len([a for a in result.artifacts if a["valid"]]) == 2


def test_correct_query_passes_contract(orchestrator, data_root):
    orchestrator.config.task_contracts = [contract(data_root)]
    result = orchestrator.run(SQL_GOAL)
    assert result.status == "finished" and result.n_retries == 0


def test_plan_cannot_omit_contract(orchestrator, data_root):
    item = contract(data_root)
    item["artifact"] = "omitted"
    orchestrator.config.task_contracts = [item]
    with pytest.raises(ValueError, match="omits required"):
        orchestrator.run(SQL_GOAL)


def test_unsupported_contract_is_rejected():
    from agent.core.config import AgentConfig
    with pytest.raises(ValueError, match="unsupported"):
        AgentConfig(task_contracts=[{"kind": "guess"}])


def test_task_contract_cannot_be_disabled_by_plan_verify_flags(orchestrator, data_root):
    class DisabledChecksPlanner(WrongYearPlanner):
        def plan(self, goal, ctx):
            graph = super().plan(goal, ctx)
            graph.get("s1").verify = []
            return graph

    orchestrator.planner = DisabledChecksPlanner()
    orchestrator.config.task_contracts = [contract(data_root)]
    assert orchestrator.run(SQL_GOAL).n_failed == 1


def test_wrong_metric_is_detected(orchestrator, data_root):
    class AveragePlanner(MockPlanner):
        def plan(self, goal, ctx):
            graph = super().plan(goal, ctx)
            step = graph.get("s1")
            step.args["query"] = step.args["query"].replace("SUM(o.amount)", "AVG(o.amount)")
            return graph

    orchestrator.planner = AveragePlanner()
    orchestrator.config.task_contracts = [contract(data_root)]
    assert orchestrator.run(SQL_GOAL).n_failed == 1
