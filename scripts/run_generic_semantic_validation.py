"""Real-model initial plan + generic-contract detection and SQL repair.

One real initial plan is reused across all controlled fault/policy groups.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import make_demo_data
from run_live_validation import RecordingClient, StrictPlanner
from run_repair_benchmark import Meter
from agent.core.config import AgentConfig
from agent.core.context import profile_data_sources
from agent.core.orchestrator import Orchestrator
from agent.core.types import TaskGraph, Artifact
from agent.llm.client import LLMClient
from agent.planning.replanner import Replanner, FeedbackReplanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY
from agent.verification.task import check_contract


class Replay:
    label = "replayed-live-plan"

    def __init__(self, plan, fault):
        self.plan_data, self.fault = plan, fault

    def plan(self, goal, ctx):
        graph = TaskGraph.from_dict(json.loads(json.dumps(self.plan_data)))
        sql = next(s for s in graph.steps if s.tool == "sql_query" and s.output_name == "monthly_orders")
        before = sql.args["query"]
        if self.fault == "wrong_filter":
            sql.args["query"] = re.sub(r"202[45]", lambda m: str(int(m[0]) + 1), before)
        elif self.fault == "wrong_metric":
            sql.args["query"] = re.sub(r"\bSUM\s*\(", "AVG(", before, flags=re.I)
        if self.fault != "control" and before == sql.args["query"]:
            raise ValueError("initial model query cannot support this fault injection")
        return graph


def main():
    client = LLMClient()
    if not client.is_live:
        raise SystemExit("configure OPENAI_* for real planning and repair")
    (ROOT / "outputs").mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="generic_semantic_", dir=ROOT / "outputs"))
    data = root / "data"
    make_demo_data.DATA_DIR = data
    make_demo_data.make_orders_db()
    contract = {"kind": "tabular_aggregate", "artifact": "monthly_orders", "source": str(data / "orders.db"),
                "source_type": "sqlite", "table": "orders",
                "filters": [{"column": "order_date", "op": "gte", "type": "text", "value": "2024-01-01"},
                            {"column": "order_date", "op": "lt", "type": "text", "value": "2025-01-01"}],
                "group_by": [{"column": "order_date", "transform": "month", "output": "month"}],
                "metrics": [{"op": "sum", "column": "amount", "output": "total"}, {"op": "count", "output": "n_orders"}]}
    (root / "contracts.json").write_text(json.dumps([contract], indent=2), encoding="utf-8")
    goal = "查询 orders.db 的 orders 表，按月统计 2024 年订单消费总额 total 和订单数 n_orders，month 为 YYYY-MM，输出 monthly_orders 并生成 final_report；使用 sql_query 完成聚合。"
    planning = root / "planning"
    planning.mkdir()
    recording = RecordingClient(planning)
    ctx = profile_data_sources(str(data))
    ctx.task_contracts = [contract]
    plan = StrictPlanner(client=recording).plan(goal, ctx).to_dict()
    results = []
    for fault in ("control", "wrong_filter", "wrong_metric"):
        for group in ("old_checks", "contract_only", "contract_and_repair"):
            folder = root / fault / group
            folder.mkdir(parents=True)
            meter = Meter(client, folder)
            config = AgentConfig(data_root=str(data), outputs_root=str(folder), task_contracts=[] if group == "old_checks" else [contract])
            policy = FeedbackReplanner(meter) if group == "contract_and_repair" else Replanner()
            summary = Orchestrator(Replay(plan, fault), policy, REGISTRY, StateManager.new_run, config).run(goal)
            correct = False
            if summary.status == "finished":
                art = next(a for a in summary.artifacts if a["name"] == "monthly_orders" and a["valid"])
                correct = check_contract(contract, Artifact(art["artifact_id"], art["name"], art["kind"], art["path"]), summary.run_dir).passed
            item = {"fault": fault, "group": group, "answer_correct": correct,
                    "wrong_accept": summary.status == "finished" and not correct,
                    "repair_calls": len(meter.calls), "summary": summary.to_dict()}
            results.append(item)
            (root / "results.json").write_text(json.dumps({"model": client.model, "initial_plan": "real model, strict, reused", "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(fault, group, summary.status, "correct=" + str(correct), flush=True)
    lines = ["# Generic contract / real model validation", "", "One real initial plan, reused with controlled faults; one dataset and run per group.", "", "| Case | Group | Correct | Wrong accept | Repair calls |", "|---|---|---|---|---:|"]
    for item in results:
        lines.append(f"| {item['fault']} | {item['group']} | {item['answer_correct']} | {item['wrong_accept']} | {item['repair_calls']} |")
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(root, flush=True)
    return 0 if all(r["answer_correct"] for r in results if r["group"] == "contract_and_repair") and all(not r["wrong_accept"] for r in results if r["group"] != "old_checks") else 1


if __name__ == "__main__":
    raise SystemExit(main())
