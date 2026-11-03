import pytest


def test_unanswered_spec_blocks_planning(orchestrator):
    from agent.core.task_spec import ClarificationRequired, TaskSpec

    spec = TaskSpec("Join sales", questions={"join_key": "Which join key?"})

    def unexpected_plan(*args):
        pytest.fail("planning began before clarification")

    orchestrator.planner.plan = unexpected_plan
    with pytest.raises(ClarificationRequired, match="join_key"):
        orchestrator.run(spec)


def test_spec_validates_answer_ids():
    from agent.core.task_spec import TaskSpec

    with pytest.raises(ValueError, match="unknown answer"):
        TaskSpec("Join sales", questions={"key": "Which key?"}, answers={"typo": "id"})


def test_csv_sqlite_representative_task(orchestrator, tmp_path):
    import csv
    import json
    from pathlib import Path
    from examples.demo_cross_source import prepare, CrossSourcePlanner

    spec = prepare(tmp_path)
    orchestrator.config.data_root = str(tmp_path)
    orchestrator.planner = CrossSourcePlanner()
    result = orchestrator.run(spec)
    assert result.status == "finished"
    assert result.report_path
    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "![region_chart]" in report and "## 验证与限制" in report
    assert "policy.md:2" in report and "Include signed refunds in regional net sales." in report
    artifact = next(item for item in result.artifacts if item["name"] == "sales_by_region")
    with (Path(result.run_dir) / artifact["path"]).open(newline="") as stream:
        assert list(csv.DictReader(stream)) == [
            {"region": "East", "amount": "85"},
            {"region": "West", "amount": "70"},
        ]
    trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert trace[0]["payload"]["task_spec"]["answers"]["refunds"] == "Include signed refunds"


def test_required_join_rejects_unmatched_sales(orchestrator, tmp_path):
    from examples.demo_cross_source import prepare, CrossSourcePlanner

    spec = prepare(tmp_path)
    with (tmp_path / "sales.csv").open("a", encoding="utf-8") as stream:
        stream.write("999,40\n")
    orchestrator.config.data_root = str(tmp_path)
    orchestrator.planner = CrossSourcePlanner()
    result = orchestrator.run(spec)
    assert result.status == "finished_with_failures"
    assert result.report_path is None
    from pathlib import Path
    assert "unmatched left" in Path(result.trace_path).read_text(encoding="utf-8")


def test_task_spec_detects_ambiguous_source_selection(orchestrator):
    from agent.core.task_spec import ClarificationRequired, TaskSpec

    with pytest.raises(ClarificationRequired, match="sales.csv"):
        orchestrator.run(TaskSpec("Compute sales totals"))
