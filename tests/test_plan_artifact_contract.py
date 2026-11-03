import pytest

from agent.core.types import Step, TaskGraph
from agent.planning.llm_planner import LLMPlanner
from agent.tools import REGISTRY


def plan_with_sql(args):
    return TaskGraph("test", [
        Step.from_dict({"step_id": "sql", "tool": "sql_query", "args": args, "output_name": "answer"}),
        Step.from_dict({"step_id": "report", "tool": "report", "args": {"inputs": ["answer"]},
                        "depends_on": ["sql"], "output_name": "final_report"}),
    ])


def test_plan_binds_artifact_name_to_tool_arguments():
    graph = plan_with_sql({"source": "data.db", "query": "SELECT 1"})
    LLMPlanner._validate(graph)
    assert graph.steps[0].args["name"] == "answer"
    assert graph.steps[1].args["name"] == "final_report"


def test_plan_rejects_conflicting_artifact_name():
    graph = plan_with_sql({"name": "sql_result"})
    with pytest.raises(ValueError, match="output_name"):
        LLMPlanner._validate(graph)


def test_plan_rejects_duplicate_artifact_names():
    graph = plan_with_sql({"name": "answer"})
    graph.steps[1].output_name = "answer"
    with pytest.raises(ValueError, match="duplicate output"):
        LLMPlanner._validate(graph)


def test_strict_plan_rejects_invalid_sql_arguments():
    graph = plan_with_sql({"source": "data.db", "query": 17})
    with pytest.raises(ValueError, match="query"):
        LLMPlanner._validate(graph)


def test_strict_plan_rejects_missing_sql_query():
    graph = plan_with_sql({"source": "data.db"})
    with pytest.raises(ValueError, match="query"):
        LLMPlanner._validate(graph)
