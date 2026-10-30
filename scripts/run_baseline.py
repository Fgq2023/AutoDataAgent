"""Run the six existing offline scenarios and independently check their answers.

Usage: python scripts/run_baseline.py
Generated data and evidence stay in a new outputs/baseline_* directory.
"""
from __future__ import annotations

import csv
import hashlib
import json
import platform
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import make_demo_data
from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import Replanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def clean_reference(source):
    # Independent stdlib oracle for the documented demo cleaning policy.
    result, seen = [], set()
    for row in rows(source):
        key = tuple(row.items())
        if key in seen:
            continue
        seen.add(key)
        if any(value == "" for value in row.values()):
            continue
        if any(Decimal(row[c]) < 0 for c in ("units", "price", "revenue")):
            continue
        result.append(row)
    return result


def check_answers(name, summary, data):
    run = Path(summary.run_dir)
    trace = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
    require((run / "state.db").is_file(), "missing state database")
    actions = [e["payload"]["action"] for e in trace if e["event"] == "recovery_decision"]
    expected = {
        "trend": [], "cleaning": [], "sql": [],
        "retry": ["retry"], "replan": ["retry", "replan"],
        "terminal_failure": ["retry", "replan"],
    }
    require(actions == expected[name], f"unexpected recovery actions: {actions}")
    if name == "terminal_failure":
        require(summary.status == "finished_with_failures", "failure was accepted")
        require(summary.n_failed == 1 and summary.n_skipped == 1, "wrong cascade")
        require(summary.report_path is None, "unexpected final report")
        return {"expected_failure": True, "recovery_actions": actions}
    require(summary.status == "finished", "run did not finish")
    require(summary.n_failed == 0 and summary.n_skipped == 0, "failed or skipped steps")
    require(summary.report_path and Path(summary.report_path).stat().st_size > 0, "missing report")
    require(summary.n_recovered == int(name in ("retry", "replan")), "wrong recovery count")
    if name == "sql":
        # Different query structure: no JOIN, then combine by customer id.
        with sqlite3.connect(data / "orders.db") as conn:
            names = dict(conn.execute("SELECT id, name FROM customers"))
            totals = defaultdict(lambda: [0, Decimal(0)])
            for customer, date, amount in conn.execute("SELECT customer_id, order_date, amount FROM orders"):
                if date[:4] == "2024":
                    totals[names[customer]][0] += 1
                    totals[names[customer]][1] += Decimal(str(amount))
        actual = rows(run / "artifacts/customer_totals.csv")
        require(len(actual) == len(totals), "wrong SQL result size")
        require({r["customer"] for r in actual} == set(totals), "wrong customers")
        for row in actual:
            count, amount = totals[row["customer"]]
            require(int(row["n_orders"]) == count, "wrong order count")
            require(abs(Decimal(row["total_spent"]) - amount) < Decimal("0.01"), "wrong customer total")
        return {"customers": len(actual), "recovery_actions": actions}
    clean = clean_reference(data / "sales.csv")
    actual_clean = rows(run / "artifacts/sales_clean.csv")
    require(len(actual_clean) == len(clean), "wrong cleaned row count")
    require([r["date"] for r in actual_clean] == [r["date"] for r in clean], "wrong cleaned dates")
    for actual, expected_row in zip(actual_clean, clean):
        for column in expected_row:
            if column in ("units", "price", "revenue"):
                require(Decimal(actual[column]) == Decimal(expected_row[column]), "wrong cleaned value")
            else:
                require(actual[column] == expected_row[column], "wrong cleaned value")
    if name != "cleaning":
        totals = defaultdict(lambda: defaultdict(Decimal))
        for row in clean:
            for column in ("units", "revenue"):
                totals[row["date"][:7]][column] += Decimal(row[column])
        actual = rows(run / "artifacts/sales_monthly.csv")
        require([r["month"] for r in actual] == sorted(totals), "wrong months")
        for row in actual:
            for column in ("units", "revenue"):
                require(abs(Decimal(row[column]) - totals[row["month"]][column]) < Decimal("0.01"), "wrong monthly total")
        require((run / "artifacts/sales_trend.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "invalid chart")
    return {"raw_rows": len(rows(data / "sales.csv")), "clean_rows": len(clean), "recovery_actions": actions}


def main():
    root = Path(tempfile.mkdtemp(prefix="baseline_", dir=ROOT / "outputs"))
    data = root / "data"
    make_demo_data.DATA_DIR = data
    make_demo_data.make_sales_csv()
    make_demo_data.make_orders_db()
    cases = [
        ("trend", "分析 sales.csv 的月度销售趋势，输出月度汇总表和趋势图", None),
        ("cleaning", "清洗 sales.csv 中的缺失值和重复行", None),
        ("sql", "查询 orders.db 中 2024 年每个客户的总消费金额", None),
        ("retry", "分析 sales.csv 的月度销售趋势", "s3"),
        ("replan", "分析 sales.csv 的月度销售趋势", "s3:permanent"),
        ("terminal_failure", "分析 sales.csv 的月度销售趋势", "s4:permanent"),
    ]
    results = []
    for name, goal, injection in cases:
        item = {"case": name}
        try:
            config = AgentConfig(data_root=str(data), outputs_root=str(root / name), mode="mock", inject_failure=injection)
            summary = Orchestrator(MockPlanner(), Replanner(), REGISTRY, StateManager.new_run, config).run(goal)
            item["summary"] = summary.to_dict()
            item["checks"] = check_answers(name, summary, data)
            item["passed"] = True
        except Exception as exc:
            item.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        results.append(item)
        print(f"{name}: {'PASS' if item['passed'] else 'FAIL'}", flush=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "executable": sys.executable,
        "packages": {p: version(p) for p in ("pandas", "matplotlib", "openpyxl")},
        "git_commit": commit.stdout.strip(),
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for folder in ("agent", "scripts") for p in sorted((ROOT / folder).rglob("*.py"))},
        "data_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in data.iterdir()},
        "mode": "mock", "results": results,
    }
    (root / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Offline scenario baseline", "", "Mode: deterministic mock; no model calls. These are existing demo scenarios, not a generalization benchmark.", "", "| Case | Check | Steps | Retry | Replan | Duration (ms) |", "|---|---|---:|---:|---:|---:|"]
    for item in results:
        s = item.get("summary", {})
        lines.append(f"| {item['case']} | {'PASS' if item['passed'] else 'FAIL'} | {s.get('n_steps', '-')} | {s.get('n_retries', '-')} | {s.get('n_replans', '-')} | {s.get('duration_ms', '-')} |")
    lines += ["", "The terminal_failure case passes only when failure and downstream skipping are correctly reported.", "", "See results.json and each case directory for answer checks, reports, state databases and traces."]
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(root)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    (ROOT / "outputs").mkdir(exist_ok=True)
    raise SystemExit(main())
