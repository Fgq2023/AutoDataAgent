"""Small controlled fault benchmark. --live uses configured model; default offline.

The offline schema oracle tests infrastructure only, not model quality.
All groups share seeded data and the same intentionally damaged template plan.
"""
import argparse
import json
import sys
import tempfile
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import make_demo_data
from run_baseline import clean_reference, rows, require
from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.llm.client import LLMClient
from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import FeedbackReplanner, Replanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY

CASES = {
    "clean_control": None,
    "chart_y": ("s4", "y", "reveneu"),
    "chart_x": ("s4", "x", "mounth"),
    "date_column": ("s3", "date_column", "order_date"),
    "group_column": ("s3", "group_by", "mounth"),
    "value_columns": ("s3", "value_columns", ["reveneu"]),
    "missing_source": ("s1", "source", "__missing_source__.csv"),
}


class FaultPlanner(MockPlanner):
    def __init__(self, fault):
        self.fault = fault

    def plan(self, goal, ctx):
        graph = super().plan(goal, ctx)
        if self.fault:
            step, key, value = self.fault
            graph.get(step).args[key] = value
        return graph


class OracleClient:
    """Deterministic known-answer repair for infrastructure checks only."""
    model = "offline-schema-oracle"
    last_call = None

    def complete(self, system, user):
        request = json.loads(user)
        args = request["step"]["args"]
        for key, correct in (("x", "month"), ("y", "revenue"), ("date_column", "date"), ("group_by", "month")):
            if key in args:
                args[key] = correct
        if "value_columns" in args:
            args["value_columns"] = ["units", "revenue"]
        return json.dumps({"args": args})


class Meter:
    def __init__(self, client, folder):
        self.client, self.folder, self.calls = client, folder, []
        self.api_key = getattr(client, "api_key", "")

    def complete(self, system, user):
        start = time.perf_counter()
        record = {"request": json.loads(user)}
        try:
            raw = self.client.complete(system, user)
            record.update(response=raw, metadata=self.client.last_call)
            return raw
        except Exception as exc:
            message = str(exc)
            if self.api_key:
                message = message.replace(self.api_key, "[REDACTED]")
            record["error"] = message
            raise
        finally:
            record["wall_ms"] = round((time.perf_counter() - start) * 1000)
            self.calls.append(record)
            (self.folder / "calls.json").write_text(json.dumps(self.calls, ensure_ascii=False, indent=2), encoding="utf-8")


def check_answer(summary, data):
    require(summary.status == "finished", "run did not finish")
    require(summary.report_path and Path(summary.report_path).is_file(), "missing report")
    run = Path(summary.run_dir)
    expected = defaultdict(lambda: defaultdict(Decimal))
    for row in clean_reference(data / "sales.csv"):
        for key in ("units", "revenue"):
            expected[row["date"][:7]][key] += Decimal(row[key])
    actual = rows(run / "artifacts/sales_monthly.csv")
    require([r["month"] for r in actual] == sorted(expected), "wrong month grouping")
    for row in actual:
        for key in ("units", "revenue"):
            require(abs(Decimal(row[key]) - expected[row["month"]][key]) < Decimal("0.01"), f"wrong {key}")
    trace = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
    plans = [e["payload"] for e in trace if e["event"] == "parameter_repair"]
    chart = next(e["payload"]["steps"] for e in trace if e["event"] == "plan_created")
    args = next(s["args"] for s in chart if s["tool"] == "chart")
    for repair in plans:
        if repair["step_id"] == "s4":
            args = repair["after"]
    require(args["x"] == "month" and args["y"] == "revenue", "wrong chart meaning")
    require((run / "artifacts/sales_trend.png").read_bytes().startswith(b"\x89PNG"), "invalid chart")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    options = parser.parse_args()
    client = LLMClient() if options.live else OracleClient()
    if options.live:
        require(client.is_live, "configure model credentials")
    (ROOT / "outputs").mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="repair_benchmark_", dir=ROOT / "outputs"))
    data = root / "data"
    make_demo_data.DATA_DIR = data
    make_demo_data.make_sales_csv()
    make_demo_data.make_orders_db()
    results = []
    for case, fault in CASES.items():
        for group in ("tool_substitution", "feedback"):
            folder = root / case / group
            folder.mkdir(parents=True)
            meter = Meter(client, folder)
            policy = Replanner() if group == "tool_substitution" else FeedbackReplanner(meter)
            config = AgentConfig(data_root=str(data), outputs_root=str(folder))
            summary = Orchestrator(FaultPlanner(fault), policy, REGISTRY, StateManager.new_run, config).run(
                "分析 sales.csv 月度趋势，清洗后按 date 的月份汇总 units 和 revenue，绘制 revenue 月度趋势图并生成报告")
            correct, error = True, None
            try:
                check_answer(summary, data)
            except (ValueError, KeyError, OSError) as exc:
                correct, error = False, str(exc)
            events = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
            counts = Counter(e["payload"]["step_id"] for e in events if e["event"] == "step_attempt")
            upstream = [s for s in counts if fault and int(s[1:]) < int(fault[0][1:])]
            rejected_ok = case == "missing_source" and summary.n_failed == 1 and not meter.calls
            item = {"case": case, "group": group, "answer_correct": correct,
                    "expected_behavior": rejected_ok if case == "missing_source" else correct,
                    "wrong_accept": summary.status == "finished" and not correct,
                    "error": error, "calls": len(meter.calls),
                    "model_ms": sum(c["wall_ms"] for c in meter.calls),
                    "usage": [c.get("metadata") for c in meter.calls],
                    "upstream_reexecutions": sum(counts[s] - 1 for s in upstream),
                    "summary": summary.to_dict()}
            results.append(item)
            (root / "results.json").write_text(json.dumps({"live": options.live, "model": client.model, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(case, group, "correct=" + str(correct), "calls=" + str(item["calls"]), flush=True)
    lines = ["# Controlled repair benchmark", "", f"Live model: {options.live}; model: {client.model}. One seeded dataset, one run per case; not a generalization estimate.", "", "| Case | Policy | Correct answer | Expected behavior | Calls | Model ms | Upstream reruns |", "|---|---|---|---|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['case']} | {r['group']} | {r['answer_correct']} | {r['expected_behavior']} | {r['calls']} | {r['model_ms']} | {r['upstream_reexecutions']} |")
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(root, flush=True)
    return 0 if all(r["expected_behavior"] for r in results if r["group"] == "feedback") else 1


if __name__ == "__main__":
    raise SystemExit(main())
