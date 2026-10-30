"""Compare tool substitution with real-model local parameter repair.

Requires configured OPENAI_* environment variables. One deliberately corrupted
template plan is used in both groups; only the repair uses a real model.
"""
import json
import sys
import tempfile
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import make_demo_data
from run_baseline import rows, clean_reference, require
from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.llm.client import LLMClient
from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import Replanner, FeedbackReplanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY


class BadChartPlanner(MockPlanner):
    def plan(self, goal, ctx):
        graph = super().plan(goal, ctx)
        graph.get("s4").args["y"] = "reveneu"
        return graph


def main():
    client = LLMClient()
    require(client.is_live, "configure a real model first")
    (ROOT / "outputs").mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="repair_", dir=ROOT / "outputs"))
    make_demo_data.DATA_DIR = root / "data"
    make_demo_data.make_sales_csv()
    make_demo_data.make_orders_db()
    results = []
    for name, replanner in [("tool_substitution", Replanner()), ("model_repair", FeedbackReplanner(client))]:
        config = AgentConfig(data_root=str(root / "data"), outputs_root=str(root / name))
        summary = Orchestrator(BadChartPlanner(), replanner, REGISTRY, StateManager.new_run, config).run("分析 sales.csv 的月度 revenue 趋势")
        events = json.loads(Path(summary.trace_path).read_text(encoding="utf-8"))
        attempts = [e["payload"]["step_id"] for e in events if e["event"] == "step_attempt"]
        item = {"group": name, "summary": summary.to_dict(), "attempted_steps": attempts}
        results.append(item)
        (root / "results.json").write_text(json.dumps({"model": client.model, "endpoint": client.base_url, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, summary.status, flush=True)
    repaired = results[1]["summary"]
    require(results[0]["summary"]["n_failed"] == 1, "baseline unexpectedly succeeded")
    require(repaired["status"] == "finished", "model repair failed; inspect trace")
    require(results[1]["attempted_steps"] == ["s1", "s2", "s3", "s4", "s4", "s4", "s5"], "unexpected upstream rerun")
    totals = defaultdict(Decimal)
    for row in clean_reference(root / "data/sales.csv"):
        totals[row["date"][:7]] += Decimal(row["revenue"])
    monthly = rows(Path(repaired["run_dir"]) / "artifacts/sales_monthly.csv")
    require([r["month"] for r in monthly] == sorted(totals), "wrong months")
    for row in monthly:
        require(abs(Decimal(row["revenue"]) - totals[row["month"]]) < Decimal("0.01"), "wrong revenue")
    (root / "checks.json").write_text(json.dumps({"passed": True, "months": len(monthly), "upstream_reused": True}), encoding="utf-8")
    print("PASS", root, flush=True)


if __name__ == "__main__":
    main()
