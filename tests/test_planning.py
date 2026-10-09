"""Subtopic A tests: mock planner intents + replanner."""

from __future__ import annotations

from agent.core.context import profile_data_sources
from agent.core.types import StepStatus
from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import Replanner

from tests.conftest import CLEAN_GOAL, SQL_GOAL, TREND_GOAL


def _ctx(data_root):
    return profile_data_sources(data_root)


# ---------------------------------------------------------------------- #
# Mock planner: intent recognition & graph shapes
# ---------------------------------------------------------------------- #
def test_trend_graph_shape(data_root):
    graph = MockPlanner().plan(TREND_GOAL, _ctx(data_root))
    ids = [s.step_id for s in graph.steps]
    tools = [s.tool for s in graph.steps]
    assert ids == ["s1", "s2", "s3", "s4", "s5"]
    assert tools == ["load_data", "clean_data", "aggregate", "chart", "report"]
    # dependency chain is a path s1 -> s2 -> s3 -> s4/s5
    assert graph.get("s2").depends_on == ["s1"]
    assert graph.get("s5").depends_on == ["s3", "s4"]
    # grounded in the real data profile
    assert graph.get("s1").args["source"].endswith("sales.csv")
    assert graph.get("s1").args["name"] == "sales_raw"


def test_trend_graph_rejects_goal_about_missing_source(data_root):
    # goal mentions a source that doesn't exist -> planner still grounds on
    # the mentioned profile name if present, else the first one
    graph = MockPlanner().plan("分析 nonexistent.csv 的趋势", _ctx(data_root))
    assert graph.get("s1").args["source"].endswith("sales.csv")  # falls back


def test_cleaning_graph_shape(data_root):
    graph = MockPlanner().plan(CLEAN_GOAL, _ctx(data_root))
    tools = [s.tool for s in graph.steps]
    assert tools == ["load_data", "clean_data", "report"]
    assert graph.get("s3").args["inputs"] == ["sales_raw", "sales_clean"]


def test_sql_graph_shape(data_root):
    graph = MockPlanner().plan(SQL_GOAL, _ctx(data_root))
    assert [s.tool for s in graph.steps] == ["sql_query", "report"]
    query = graph.get("s1").args["query"]
    assert "2024" in query          # year detected from the goal
    assert graph.get("s1").args["source"].endswith("orders.db")


def test_sql_graph_without_year_has_no_where(data_root):
    graph = MockPlanner().plan("查询 orders.db 每个客户的总消费金额", _ctx(data_root))
    assert "WHERE" not in graph.get("s1").args["query"]


def test_fallback_explore_intent(data_root):
    graph = MockPlanner().plan("随便看看有什么数据", _ctx(data_root))
    assert all(s.tool in ("load_data", "sql_query", "report") for s in graph.steps)


def test_planner_raises_without_sources(tmp_path):
    import pytest

    from agent.core.context import profile_data_sources

    empty = tmp_path / "empty_data"
    empty.mkdir()
    with pytest.raises(ValueError):
        MockPlanner().plan("anything", profile_data_sources(empty))


# ---------------------------------------------------------------------- #
# TaskGraph scheduling semantics
# ---------------------------------------------------------------------- #
def test_next_ready_respects_dependencies():
    from agent.core.types import Step, TaskGraph

    graph = TaskGraph(
        goal="g",
        steps=[
            Step("s1", "a", "load_data", {"source": "x", "name": "n"}),
            Step("s2", "b", "clean_data", {"input": "n"}, depends_on=["s1"]),
        ],
    )
    assert graph.next_ready().step_id == "s1"
    graph.mark("s1", StepStatus.SUCCEEDED)
    assert graph.next_ready().step_id == "s2"
    graph.mark("s2", StepStatus.SUCCEEDED)
    assert graph.done()


def test_cascade_skip_blocks_dependents():
    from agent.core.types import Step, StepStatus, TaskGraph

    graph = TaskGraph(
        goal="g",
        steps=[
            Step("s1", "a", "load_data", {}),
            Step("s2", "b", "clean_data", {}, depends_on=["s1"]),
            Step("s3", "c", "aggregate", {}, depends_on=["s2"]),
        ],
    )
    graph.mark("s1", StepStatus.FAILED)
    skipped = graph.cascade_skip("s1")
    assert set(skipped) == {"s2", "s3"}
    assert graph.status_of("s3") == StepStatus.SKIPPED
    assert graph.done()


# ---------------------------------------------------------------------- #
# Replanner: tool substitution
# ---------------------------------------------------------------------- #
def test_replanner_swaps_to_alternative(data_root, outputs_root):
    from agent.core.types import Step, TaskGraph

    graph = MockPlanner().plan(TREND_GOAL, _ctx(data_root))
    state = __import__("agent.state.manager", fromlist=["StateManager"]).StateManager.new_run(
        outputs_root, TREND_GOAL, graph
    )
    failed = graph.get("s3")
    new_graph = Replanner().replan(TREND_GOAL, graph, failed, state)
    assert new_graph is not None
    assert graph.get("s3").tool == "aggregate_pivot"
    assert graph.status_of("s3") == StepStatus.PENDING
    state.close()


def test_replanner_returns_none_without_alternative(outputs_root):
    from agent.core.types import Step, TaskGraph

    graph = TaskGraph(
        goal="g", steps=[Step("s1", "a", "chart", {"input": "x"})]
    )
    state = __import__("agent.state.manager", fromlist=["StateManager"]).StateManager.new_run(
        outputs_root, "g", graph
    )
    assert Replanner().replan("g", graph, graph.get("s1"), state) is None
    state.close()
