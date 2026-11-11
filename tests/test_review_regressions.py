import json
from pathlib import Path
import pytest
import pandas as pd

from agent.core.types import Step, TaskGraph
from agent.planning.llm_planner import LLMPlanner
from agent.tools.base import get_tool
from agent.tools.builtins._io import df_meta, read_df, write_df_artifact
from tests.test_tools import _FakeArtifact


@pytest.mark.parametrize("name", ["../../../overwrite", "/outside", r"..\outside"])
def test_host_write_cannot_escape(ctx, tmp_path, name):
    source = tmp_path / "source.csv"
    source.write_text("a\n1\n")
    with pytest.raises(ValueError, match="artifact name"):
        get_tool("load_data").run({"source": str(source), "name": name}, ctx)
    assert not (tmp_path / "overwrite.csv").exists()


def test_cli_goal_cannot_read_outside_data_root(orchestrator, tmp_path):
    outside = tmp_path / "private.csv"
    outside.write_text("secret\nprivate\n")

    class Planner:
        def plan(self, goal, ctx):
            return TaskGraph(goal, [Step("load", "load", "load_data",
                              {"source": str(outside), "name": "outside"}, output_name="outside")])

    orchestrator.planner = Planner()
    with pytest.raises(ValueError, match="source outside"):
        orchestrator.run("read data")


def test_text_ids_survive_aggregation_and_join(ctx):
    spec = write_df_artifact(ctx.run_dir, "codes", pd.DataFrame({"code": ["001", "1"], "amount": [12, 8]}))
    art = _FakeArtifact("codes", spec.path)
    art.meta = spec.meta
    result = get_tool("aggregate").run({"input": art, "name": "totals",
                                       "group_by": "code", "value_columns": ["amount"]}, ctx)[0]
    out = _FakeArtifact("totals", result.path)
    out.meta = result.meta
    frame = read_df(ctx.run_dir, out)
    assert dict(zip(frame.code, frame.amount)) == {"001": 12, "1": 8}
    right = write_df_artifact(ctx.run_dir, "labels", pd.DataFrame({"code": ["001", "1"], "label": ["A", "B"]}))
    right_art = _FakeArtifact("labels", right.path)
    right_art.meta = right.meta
    joined = get_tool("join").run({"left": out, "right": right_art, "on": "code",
               "how": "left", "cardinality": "one_to_one", "name": "joined"}, ctx)[0]
    assert joined.meta["n_rows"] == 2


def test_unknown_verify_flag_rejected():
    graph = TaskGraph("goal", [Step("s", "load", "load_data", {"source": "data.csv", "name": "x"},
                                  verify=["schem"], output_name="x")])
    with pytest.raises(ValueError, match="verification flag"):
        LLMPlanner._validate(graph)


def test_unselected_bad_json_is_recorded_not_fatal(data_root, outputs_root):
    from agent.service import RunService
    from agent.core.config import AgentConfig
    from agent.core.task_spec import TaskSpec
    (data_root / "broken.json").write_text("{")
    with RunService(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))) as service:
        assert next(s for s in service.sources() if s["path"] == "broken.json")["issues"]
        job = service.start(TaskSpec("清洗 sales.csv 中的缺失值和重复行", sources=["sales.csv"]))
        service.wait(job["job_id"])
        assert service.status(job["job_id"])["status"] == "finished"
from tests.test_tools import ctx


def test_text_ids_survive_python_execution(ctx):
    spec = write_df_artifact(ctx.run_dir, "python_codes", pd.DataFrame({"code": ["001", "1"], "amount": [12, 8]}))
    art = _FakeArtifact("python_codes", spec.path)
    art.meta = spec.meta
    result = get_tool("python_exec").run({"input": art, "name": "python_totals",
                 "code": "result = df.groupby('code', as_index=False)['amount'].sum()"}, ctx)[0]
    output = _FakeArtifact("python_totals", result.path)
    output.meta = result.meta
    frame = read_df(ctx.run_dir, output)
    assert dict(zip(frame.code, frame.amount)) == {"001": 12, "1": 8}
