"""Shared pytest fixtures — fully self-contained (no repo data files needed)."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from agent.core.config import AgentConfig
from agent.core.context import profile_data_sources
from agent.core.orchestrator import Orchestrator
from agent.core.types import Step, TaskGraph
from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import Replanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY  # noqa: F401  (registers every tool)


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    """A small data root with a dirty CSV + a SQLite DB, like the demo data."""
    root = tmp_path / "data"
    root.mkdir()

    rows = []
    for i in range(6):  # 2024-01 .. 2024-06
        month = f"2024-{i + 1:02d}"
        for region in ("east", "west"):
            units = 10 + i * 2
            price = 3.0
            rows.append([f"{month}-15", region, "A", units, price, round(units * price, 2)])
    # dirty rows: missing revenue, duplicate, negative price
    rows[3][5] = ""
    rows.append(list(rows[10]))          # exact duplicate of row 10
    rows[11][4] = -1.0                  # negative price on the last row
    rows[11][5] = -30.0

    df = pd.DataFrame(
        rows, columns=["date", "region", "product", "units", "price", "revenue"]
    )
    df.to_csv(root / "sales.csv", index=False)

    db = sqlite3.connect(str(root / "orders.db"))
    db.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER,
                             order_date TEXT, amount REAL);
        INSERT INTO customers VALUES (1, 'alice', 'sz'), (2, 'bob', 'gz');
        INSERT INTO orders VALUES (1, 1, '2024-03-01', 100.0),
                                  (2, 1, '2024-04-01', 50.0),
                                  (3, 2, '2024-05-01', 75.0),
                                  (4, 2, '2025-01-01', 500.0);
        """
    )
    db.commit()
    db.close()
    return root


@pytest.fixture()
def outputs_root(tmp_path: Path) -> Path:
    out = tmp_path / "outputs"
    out.mkdir()
    return out


@pytest.fixture()
def config(data_root: Path, outputs_root: Path) -> AgentConfig:
    return AgentConfig(
        data_root=str(data_root),
        outputs_root=str(outputs_root),
        mode="mock",
        exec_timeout_s=30.0,
    )


@pytest.fixture()
def orchestrator(config: AgentConfig) -> Orchestrator:
    return Orchestrator(
        planner=MockPlanner(),
        replanner=Replanner(),
        registry=REGISTRY,
        state_factory=StateManager.new_run,
        config=config,
    )


TREND_GOAL = "分析 sales.csv 的月度销售趋势，输出月度汇总表和趋势图"
CLEAN_GOAL = "清洗 sales.csv 中的缺失值和重复行"
SQL_GOAL = "查询 orders.db 中 2024 年每个客户的总消费金额"


def run_goal(orchestrator: Orchestrator, goal: str,
             inject_failure: str | None = None) -> "object":
    """Run with (optional) failure injection; returns the RunSummary."""
    orchestrator.config.inject_failure = inject_failure
    return orchestrator.run(goal)


def make_graph(goal: str, steps: list[Step]) -> TaskGraph:
    return TaskGraph(goal=goal, steps=steps)


__all__ = [
    "data_root", "outputs_root", "config", "orchestrator",
    "TREND_GOAL", "CLEAN_GOAL", "SQL_GOAL", "run_goal", "make_graph",
    "textwrap",
]
