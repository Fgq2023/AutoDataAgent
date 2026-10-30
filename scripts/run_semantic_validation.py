"""Contrast silent SQL mistakes, detection only, and real-model repair."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import make_demo_data
from run_repair_benchmark import Meter
from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.llm.client import LLMClient
from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import Replanner, FeedbackReplanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY
from agent.verification.task import check_contract
from agent.core.types import Artifact


class FaultPlanner(MockPlanner):
    def __init__(self, fault):
        self.fault = fault

    def plan(self, goal, ctx):
        graph = super().plan(goal, ctx)
        step = graph.get("s1")
        if self.fault == "wrong_year":
            step.args["query"] = step.args["query"].replace("2024", "2025")
        elif self.fault == "wrong_metric":
            step.args["query"] = step.args["query"].replace("SUM(o.amount)", "AVG(o.amount)")
        return graph


def main():
    client = LLMClient()
    if not client.is_live:
        raise SystemExit("configure OPENAI_* for a real model")
    (ROOT / "outputs").mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="semantic_", dir=ROOT / "outputs"))
    data = root / "data"
    make_demo_data.DATA_DIR = data
    make_demo_data.make_sales_csv()
    make_demo_data.make_orders_db()
    contract = {"kind": "customer_totals_year", "artifact": "customer_totals", "source": str(data / "orders.db"), "year": 2024}
    (root / "contracts.json").write_text(json.dumps([contract], indent=2), encoding="utf-8")
    results = []
    for fault in ("correct_control", "wrong_year", "wrong_metric"):
        for group in ("old_checks", "contract_only", "contract_and_repair"):
            folder = root / fault / group
            folder.mkdir(parents=True)
            meter = Meter(client, folder)
            policy = FeedbackReplanner(meter) if group == "contract_and_repair" else Replanner()
            config = AgentConfig(data_root=str(data), outputs_root=str(folder), task_contracts=[] if group == "old_checks" else [contract])
            summary = Orchestrator(FaultPlanner(fault), policy, REGISTRY, StateManager.new_run, config).run("查询 orders.db 中 2024 年每个客户的订单数与总消费金额，生成报告")
            correct = False
            if summary.status == "finished":
                art = Artifact("customer_totals", "customer_totals", "table", "artifacts/customer_totals.csv")
                correct = check_contract(contract, art, summary.run_dir).passed
            item = {"fault": fault, "group": group, "answer_correct": correct,
                    "wrong_accept": summary.status == "finished" and not correct,
                    "calls": len(meter.calls), "summary": summary.to_dict()}
            results.append(item)
            (root / "results.json").write_text(json.dumps({"model": client.model, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(fault, group, summary.status, "correct=" + str(correct), flush=True)
    lines = ["# Task semantic validation", "", "Controlled template-plan faults; real model used only for repair. One dataset and run per case.", "", "| Case | Group | Correct | Wrong accept | Model calls |", "|---|---|---|---|---:|"]
    for item in results:
        lines.append(f"| {item['fault']} | {item['group']} | {item['answer_correct']} | {item['wrong_accept']} | {item['calls']} |")
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(root, flush=True)
    expected = all(r["answer_correct"] for r in results if r["group"] == "contract_and_repair")
    expected &= all(not r["wrong_accept"] for r in results if r["group"] != "old_checks")
    return 0 if expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
