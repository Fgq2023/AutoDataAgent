"""Validate real model planning without allowing mock fallback.

Set OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_MODEL in the process environment.
Only generated demo data/schema are used. Credentials are never saved.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import make_demo_data
from run_baseline import check_answers
from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.llm.client import LLMClient
from agent.planning.llm_planner import LLMPlanner
from agent.planning.replanner import Replanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY


class RecordingClient(LLMClient):
    def __init__(self, folder):
        super().__init__()
        self.folder = folder

    def complete(self, system, user, **kwargs):
        (self.folder / "request.json").write_text(json.dumps({"system": system, "user": json.loads(user)}, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = super().complete(system, user, **kwargs)
        (self.folder / "response.txt").write_text(raw, encoding="utf-8")
        return raw


class StrictPlanner(LLMPlanner):
    label = "llm-strict"

    def plan(self, goal, ctx):
        if not self.client.is_live:
            raise ValueError("A live model must be configured")
        graph = self._ask(goal, ctx)
        self._validate(graph)
        (self.client.folder / "plan.json").write_text(json.dumps(graph.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return graph


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in the process environment")
    (ROOT / "outputs").mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="live_", dir=ROOT / "outputs"))
    data = root / "data"
    make_demo_data.DATA_DIR = data
    make_demo_data.make_sales_csv()
    make_demo_data.make_orders_db()
    cases = [
        ("trend", "分析 sales.csv 的月度销售趋势。先清洗，再汇总 units 和 revenue，绘制 revenue 趋势图并生成报告。产物名称依次为 sales_raw、sales_clean、sales_monthly、sales_trend、final_report。"),
        ("cleaning", "清洗 sales.csv 的重复行、缺失值和非法负值，输出清洗前后报告。产物名称为 sales_raw、sales_clean、final_report。"),
        ("sql", "查询 orders.db 中 2024 年每位客户的订单数和消费总额，按总额降序输出并生成报告。结果列名 customer、n_orders、total_spent；产物名称 customer_totals、final_report。"),
    ]
    results = []
    for name, goal in cases:
        folder = root / name
        folder.mkdir()
        client = RecordingClient(folder)
        item = {"case": name, "model": client.model, "endpoint": client.base_url, "mock_fallback": False}
        try:
            config = AgentConfig(data_root=str(data), outputs_root=str(folder), mode="llm")
            summary = Orchestrator(StrictPlanner(client=client), Replanner(), REGISTRY, StateManager.new_run, config).run(goal)
            item["summary"] = summary.to_dict()
            item["checks"] = check_answers(name, summary, data)
            item["passed"] = True
        except Exception as exc:
            # Providers may echo request details in errors; scrub credentials.
            message = f"{type(exc).__name__}: {exc}".replace(client.api_key, "[REDACTED]")
            item.update(passed=False, error=message)
        results.append(item)
        (root / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{name}: {'PASS' if item['passed'] else item['error']}", flush=True)
        if not item["passed"] and "LLM HTTP" in item.get("error", ""):
            break  # don't repeat authentication/billing failures
    print(root, flush=True)
    return 0 if len(results) == len(cases) and all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
