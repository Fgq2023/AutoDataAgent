"""Deterministic template planner (subtopic A, mock mode).

The mock planner recognises a small set of task *intents* from keywords in
the goal and emits a parameterised task graph grounded in the actual data
profiles.  It is:

  * fully offline — no API key, no network
  * deterministic — the same goal always yields the same graph, so the
    whole system is unit-testable and CI-safe
  * interface-identical to the LLM planner — swapping planners never
    touches the rest of the system
"""

from __future__ import annotations

import re
from typing import Any

from agent.core.context import DataContext, DataProfile
from agent.core.types import Step, TaskGraph
from agent.planning.base import BasePlanner

_INTENTS: dict[str, list[str]] = {
    "trend": [
        "趋势", "trend", "月度", "monthly", "汇总", "summary",
        "图表", "chart", "分析", "季度", "quarterly", "统计",
    ],
    "cleaning": [
        "清洗", "clean", "缺失", "missing", "重复", "duplicate",
        "去重", "脏数据", "dirty", "质量",
    ],
    "sql": [
        "sql", "数据库", "database", ".db", "订单", "orders",
        "客户", "customer", "消费", "总金额", "每个客户",
    ],
}


class MockPlanner(BasePlanner):
    label = "mock"

    # ------------------------------------------------------------------ #
    def plan(self, goal: str, ctx: DataContext) -> TaskGraph:
        intent = self._match_intent(goal)
        builder = getattr(self, f"_intent_{intent}")
        return builder(goal, ctx)

    # ------------------------------------------------------------------ #
    # Intent matching
    # ------------------------------------------------------------------ #
    @staticmethod
    def _match_intent(goal: str) -> str:
        scores = {
            intent: sum(1 for kw in kws if kw.lower() in goal.lower())
            for intent, kws in _INTENTS.items()
        }
        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else "explore"

    @staticmethod
    def _pick_profile(goal: str, profiles: list[DataProfile],
                      kinds: tuple[str, ...]) -> DataProfile:
        """Prefer a profile whose name is mentioned in the goal; else first."""
        if not profiles:
            raise ValueError(
                f"no data sources of kind {kinds} found — cannot plan this goal"
            )
        lowered = goal.lower()
        for p in profiles:
            if p.name.lower() in lowered:
                return p
        return profiles[0]

    @staticmethod
    def _numeric_columns(prof: DataProfile) -> list[str]:
        return [
            c["name"]
            for c in prof.schema.get("columns", [])
            if "int" in c.get("dtype", "") or "float" in c.get("dtype", "")
        ]

    @staticmethod
    def _date_column(prof: DataProfile) -> str | None:
        for c in prof.schema.get("columns", []):
            if "date" in c["name"].lower() or "日期" in c["name"]:
                return c["name"]
        return None

    # ------------------------------------------------------------------ #
    # Intent: trend analysis  (load -> clean -> aggregate -> chart -> report)
    # ------------------------------------------------------------------ #
    def _intent_trend(self, goal: str, ctx: DataContext) -> TaskGraph:
        prof = self._pick_profile(goal, ctx.tabular_profiles(), ("csv", "xlsx"))
        base = prof.name

        date_column = self._date_column(prof)
        numeric = self._numeric_columns(prof)
        value_columns = [
            c for c in numeric
            if "price" not in c.lower() and "单价" not in c
        ]
        y = "revenue" if "revenue" in value_columns else (
            value_columns[0] if value_columns else numeric[0]
        )

        if date_column is not None:
            group_by, x = "month", "month"
        else:
            non_numeric = [
                c["name"] for c in prof.schema.get("columns", [])
                if c["name"] not in numeric
            ]
            group_by = x = non_numeric[0] if non_numeric else (value_columns[0] if value_columns else y)

        return TaskGraph(
            goal=goal,
            steps=[
                Step(
                    step_id="s1",
                    description=f"加载 {prof.path}",
                    tool="load_data",
                    args={"source": prof.path, "name": f"{base}_raw"},
                    depends_on=[],
                    verify=["schema"],
                    output_name=f"{base}_raw",
                ),
                Step(
                    step_id="s2",
                    description="清洗数据（去重、缺失值、非法负值）",
                    tool="clean_data",
                    args={"input": f"{base}_raw", "name": f"{base}_clean"},
                    depends_on=["s1"],
                    verify=["schema", "semantic"],
                    output_name=f"{base}_clean",
                ),
                Step(
                    step_id="s3",
                    description=f"按 {group_by} 聚合 {value_columns or y}",
                    tool="aggregate",
                    args={
                        "input": f"{base}_clean",
                        "name": f"{base}_monthly",
                        "group_by": group_by,
                        "date_column": date_column,
                        "value_columns": value_columns or [y],
                    },
                    depends_on=["s2"],
                    verify=["schema", "semantic"],
                    output_name=f"{base}_monthly",
                ),
                Step(
                    step_id="s4",
                    description=f"绘制 {y} 趋势图",
                    tool="chart",
                    args={
                        "input": f"{base}_monthly",
                        "x": x,
                        "y": y,
                        "title": f"Monthly {y} Trend ({base})",
                        "name": f"{base}_trend",
                    },
                    depends_on=["s3"],
                    verify=["schema"],
                    output_name=f"{base}_trend",
                ),
                Step(
                    step_id="s5",
                    description="生成分析报告",
                    tool="report",
                    args={
                        "inputs": [f"{base}_monthly", f"{base}_trend"],
                        "name": "final_report",
                    },
                    depends_on=["s3", "s4"],
                    verify=["schema"],
                    output_name="final_report",
                ),
            ],
        )

    # ------------------------------------------------------------------ #
    # Intent: data cleaning  (load -> clean -> compare report)
    # ------------------------------------------------------------------ #
    def _intent_cleaning(self, goal: str, ctx: DataContext) -> TaskGraph:
        prof = self._pick_profile(goal, ctx.tabular_profiles(), ("csv", "xlsx"))
        base = prof.name
        return TaskGraph(
            goal=goal,
            steps=[
                Step(
                    step_id="s1",
                    description=f"加载 {prof.path}",
                    tool="load_data",
                    args={"source": prof.path, "name": f"{base}_raw"},
                    depends_on=[],
                    verify=["schema"],
                    output_name=f"{base}_raw",
                ),
                Step(
                    step_id="s2",
                    description="清洗数据（去重、缺失值、非法负值）",
                    tool="clean_data",
                    args={"input": f"{base}_raw", "name": f"{base}_clean"},
                    depends_on=["s1"],
                    verify=["schema", "semantic"],
                    output_name=f"{base}_clean",
                ),
                Step(
                    step_id="s3",
                    description="生成清洗前后对比报告",
                    tool="report",
                    args={
                        "inputs": [f"{base}_raw", f"{base}_clean"],
                        "name": "final_report",
                        "title": f"数据清洗报告：{base}",
                    },
                    depends_on=["s1", "s2"],
                    verify=["schema"],
                    output_name="final_report",
                ),
            ],
        )

    # ------------------------------------------------------------------ #
    # Intent: SQL analysis  (sql_query -> report)
    # ------------------------------------------------------------------ #
    def _intent_sql(self, goal: str, ctx: DataContext) -> TaskGraph:
        prof = self._pick_profile(goal, ctx.sqlite_profiles(), ("sqlite",))
        year = self._detect_year(goal)
        where = f"WHERE o.order_date LIKE '{year}%'" if year else ""
        query = (
            "SELECT c.name AS customer, "
            "COUNT(*) AS n_orders, "
            "ROUND(SUM(o.amount), 2) AS total_spent "
            "FROM orders o JOIN customers c ON o.customer_id = c.id "
            f"{where} "
            "GROUP BY c.name "
            "ORDER BY total_spent DESC"
        )
        return TaskGraph(
            goal=goal,
            steps=[
                Step(
                    step_id="s1",
                    description=f"查询 {prof.path}",
                    tool="sql_query",
                    args={
                        "source": prof.path,
                        "query": query,
                        "name": "customer_totals",
                    },
                    depends_on=[],
                    verify=["schema", "semantic"],
                    output_name="customer_totals",
                ),
                Step(
                    step_id="s2",
                    description="生成 SQL 分析报告",
                    tool="report",
                    args={"inputs": ["customer_totals"], "name": "final_report"},
                    depends_on=["s1"],
                    verify=["schema"],
                    output_name="final_report",
                ),
            ],
        )

    # ------------------------------------------------------------------ #
    # Intent: explore (fallback)  (load each source -> schema report)
    # ------------------------------------------------------------------ #
    def _intent_explore(self, goal: str, ctx: DataContext) -> TaskGraph:
        profiles = ctx.profiles[:2]
        if not profiles:
            raise ValueError(
                "no supported data sources found under the data root — "
                "add CSV/XLSX/JSON/SQLite files first"
            )
        steps: list[Step] = []
        loaded: list[str] = []
        for i, prof in enumerate(profiles, start=1):
            name = f"{prof.name}_raw"
            steps.append(
                Step(
                    step_id=f"s{i}",
                    description=f"加载 {prof.path}",
                    tool="load_data" if prof.kind in ("csv", "xlsx", "json") else "sql_query",
                    args={
                        "source": prof.path,
                        "name": name,
                        **({"query": f"SELECT * FROM sqlite_master LIMIT 5"} if prof.kind == "sqlite" else {}),
                    },
                    depends_on=[],
                    verify=["schema"],
                    output_name=name,
                )
            )
            loaded.append(name)
        steps.append(
            Step(
                step_id=f"s{len(steps) + 1}",
                description="生成数据概览报告",
                tool="report",
                args={"inputs": loaded, "name": "final_report", "title": "数据概览"},
                depends_on=[f"s{i}" for i in range(1, len(loaded) + 1)],
                verify=["schema"],
                output_name="final_report",
            )
        )
        return TaskGraph(goal=goal, steps=steps)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _detect_year(goal: str) -> str | None:
        m = re.search(r"(20\d{2})", goal)
        return m.group(1) if m else None
