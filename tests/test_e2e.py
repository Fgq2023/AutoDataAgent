"""End-to-end tests: full orchestrator runs, incl. failure-injection demos."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import CLEAN_GOAL, SQL_GOAL, TREND_GOAL, run_goal


def _trace(summary) -> list[dict]:
    return json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- #
# Scenario 1: trend analysis (the headline E2E demo)
# ---------------------------------------------------------------------- #
def test_trend_analysis_full_run(orchestrator, data_root, outputs_root):
    summary = run_goal(orchestrator, TREND_GOAL)

    assert summary.status == "finished"
    assert summary.n_steps == 5
    assert summary.n_succeeded == 5
    assert summary.n_failed == 0 and summary.n_skipped == 0
    assert summary.report_path is not None

    run_dir = Path(summary.run_dir)
    monthly = run_dir / "artifacts" / "sales_monthly.csv"
    chart = run_dir / "artifacts" / "sales_trend.png"
    report = run_dir / "report.md"
    assert monthly.is_file() and chart.is_file() and report.is_file()

    import pandas as pd

    m = pd.read_csv(monthly)
    assert len(m) == 6  # six months of fixture data
    assert m["month"].tolist() == sorted(m["month"].tolist())
    raw = pd.read_csv(data_root / "sales.csv")
    clean = pd.read_csv(run_dir / "artifacts" / "sales_clean.csv")
    assert len(clean) < len(raw)  # dirty rows were dropped
    assert chart.read_bytes()[:4] == b"\x89PNG"


def test_trend_report_contains_summary_provenance_and_figure(orchestrator):
    summary = run_goal(orchestrator, TREND_GOAL)
    text = Path(summary.report_path).read_text(encoding="utf-8")
    assert "## 执行摘要" in text
    assert "## 数据溯源" in text
    assert "[s1] load_data" in text
    assert "sales_trend" in text and "artifacts/sales_trend.png" in text
    assert "## 结果" in text


def test_trace_records_every_event_kind(orchestrator):
    summary = run_goal(orchestrator, TREND_GOAL)
    kinds = {e["event"] for e in _trace(summary)}
    expected = {
        "run_started", "plan_created", "step_attempt", "verification",
        "step_succeeded", "step_finished", "run_finished",
    }
    assert expected <= kinds
    # every step attempt has a verification verdict attached
    verdicts = [e for e in _trace(summary) if e["event"] == "verification"]
    assert len(verdicts) == 5
    assert all(v["payload"]["ok"] for v in verdicts)


def test_state_db_keeps_provenance_chain(orchestrator):
    import sqlite3

    summary = run_goal(orchestrator, TREND_GOAL)
    conn = sqlite3.connect(str(Path(summary.run_dir) / "state.db"))
    prov = dict(
        conn.execute("SELECT artifact_id, step_id FROM provenance").fetchall()
    )
    conn.close()
    # the chain: raw -> clean -> monthly -> trend + report
    assert prov["sales_raw"] == "s1"
    assert prov["sales_clean"] == "s2"
    assert prov["sales_monthly"] == "s3"
    assert prov["sales_trend"] == "s4"
    assert prov["final_report"] == "s5"


# ---------------------------------------------------------------------- #
# Scenario 2: data cleaning
# ---------------------------------------------------------------------- #
def test_cleaning_scenario(orchestrator):
    summary = run_goal(orchestrator, CLEAN_GOAL)
    assert summary.status == "finished"
    assert summary.n_succeeded == 3
    text = Path(summary.report_path).read_text(encoding="utf-8")
    assert "sales_raw" in text and "sales_clean" in text


# ---------------------------------------------------------------------- #
# Scenario 3: SQL question answering
# ---------------------------------------------------------------------- #
def test_sql_scenario(orchestrator):
    import pandas as pd

    summary = run_goal(orchestrator, SQL_GOAL)
    assert summary.status == "finished"
    totals = pd.read_csv(Path(summary.run_dir) / "artifacts" / "customer_totals.csv")
    assert list(totals.columns) == ["customer", "n_orders", "total_spent"]
    by_name = dict(zip(totals["customer"], totals["total_spent"]))
    assert by_name["alice"] == 150.0  # 2024 only: 100 + 50
    assert by_name["bob"] == 75.0      # the 500 order is 2025


# ---------------------------------------------------------------------- #
# Failure injection: transient (RETRY) and permanent (REPLAN)
# ---------------------------------------------------------------------- #
def test_transient_failure_is_retried_and_recovered(orchestrator):
    summary = run_goal(orchestrator, TREND_GOAL, inject_failure="s3")
    assert summary.status == "finished"
    assert summary.n_recovered == 1
    assert summary.n_retries == 1 and summary.n_replans == 0

    kinds = [e["event"] for e in _trace(summary)]
    assert "failure_injected" in kinds
    assert "recovery_decision" in kinds
    decisions = [
        e["payload"]["action"]
        for e in _trace(summary)
        if e["event"] == "recovery_decision"
    ]
    assert decisions == ["retry"]


def test_permanent_failure_is_rescued_by_replan(orchestrator):
    summary = run_goal(orchestrator, TREND_GOAL, inject_failure="s3:permanent")
    assert summary.status == "finished"
    assert summary.n_recovered == 1
    assert summary.n_replans == 1

    replans = [
        e["payload"] for e in _trace(summary) if e["event"] == "replan"
    ]
    assert replans == [
        {"step_id": "s3", "from_tool": "aggregate",
         "to_tool": "aggregate_pivot",
         "reason": "recovery policy chose REPLAN"}
    ]
    # the step that succeeded used the alternative tool
    attempts = [
        e["payload"]["tool"] for e in _trace(summary)
        if e["event"] == "step_attempt" and e["payload"]["step_id"] == "s3"
    ]
    assert attempts == ["aggregate", "aggregate", "aggregate_pivot"]
    # ...and the run still produced the full artifact chain
    assert (Path(summary.run_dir) / "artifacts" / "sales_monthly.csv").is_file()


def test_unrecoverable_failure_cascades_skip(orchestrator):
    # chart has no replan alternative and no retry left after 2 failures:
    # inject permanent failure at s4 -> RETRY once, then REPLAN is impossible
    # -> FAIL + cascade skip of s5
    summary = run_goal(orchestrator, TREND_GOAL, inject_failure="s4:permanent")
    assert summary.status == "finished_with_failures"
    assert summary.n_failed == 1
    assert summary.n_skipped == 1          # s5 report skipped
    assert summary.report_path is None     # no final report produced
    skipped = [
        e["payload"]["skipped"] for e in _trace(summary)
        if e["event"] == "cascade_skip"
    ]
    assert skipped == [["s5"]]
    # CLI-style exit code logic would be non-zero; the summary says it:
    assert summary.n_failed == 1


def test_verify_failure_is_classified_and_recovered(orchestrator, monkeypatch):
    # sabotage the aggregate tool once: it produces an artifact whose meta
    # lies about its row count -> schema check must fail -> verify-retry
    from agent.tools.builtins import aggregate as agg_mod

    original = agg_mod.AggregateTool.run
    calls = {"n": 0}

    def sabotaged(self, args, ctx):
        specs = original(self, args, ctx)
        calls["n"] += 1
        if calls["n"] == 1:
            specs[0].meta["n_rows"] = 999  # inconsistent with the file
        return specs

    monkeypatch.setattr(agg_mod.AggregateTool, "run", sabotaged)
    summary = run_goal(orchestrator, TREND_GOAL)
    assert summary.status == "finished"
    assert calls["n"] == 2                   # first attempt failed verification
    verdicts = [
        e["payload"] for e in _trace(summary) if e["event"] == "verification"
    ]
    assert verdicts[2]["ok"] is False        # s3 attempt 1: caught the drift
    assert any("drift" in c["detail"] for c in verdicts[2]["checks"])
    assert summary.n_recovered == 1
