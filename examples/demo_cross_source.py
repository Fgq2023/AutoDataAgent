"""Synthetic cross-source task. Prepare inputs for the CLI or run its offline reference plan."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.core.task_spec import TaskSpec
from agent.core.types import Step, TaskGraph
from agent.planning.replanner import Replanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "sales.csv").open("x", encoding="utf-8") as stream:
        stream.write("customer_id,amount\n1,100\n1,-15\n2,70\n")
    with closing(sqlite3.connect(root / "customers.db")) as db, db:
        db.executescript("CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, region TEXT);"
                         "INSERT INTO customers VALUES (1, 'East'), (2, 'West');")
    (root / "policy.md").write_text("# Refund policy\nInclude signed refunds in regional net sales.\n",
                                    encoding="utf-8")
    spec = TaskSpec(
        goal="Join sales.csv with customers in customers.db by customer_id "
             "(many-to-one). Require every sale to match a customer. Sum amount by region, chart the totals, and generate final_report.",
        sources=["sales.csv", "customers.db", "policy.md"],
        metrics={"amount": "SUM(amount), including signed refunds"},
        group_by=["region"],
        rule_sources={"refunds": {"source": "policy.md", "line": 2}},
        questions={"refunds": "How should negative sales amounts be handled?"},
        answers={"refunds": "Include signed refunds"},
        output_columns={"sales_by_region": ["region", "amount"]},
    )
    (root / "task.json").write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")
    return spec


class CrossSourcePlanner:
    label = "offline-cross-source-reference"

    def plan(self, goal, ctx):
        sources = {profile.name: profile.path for profile in ctx.profiles}
        return TaskGraph(goal, [
            Step("sales", "Read sales", "load_data", {"source": sources["sales"], "name": "sales"},
                 output_name="sales"),
            Step("customers", "Query customer regions", "sql_query",
                 {"source": sources["customers"], "name": "regions",
                  "query": "SELECT customer_id, region FROM customers"},
                 output_name="regions"),
            Step("join", "Join by customer ID", "join",
                 {"left": "sales", "right": "regions", "name": "joined", "on": "customer_id",
                  "how": "left", "cardinality": "many_to_one", "require_left_match": True}, ["sales", "customers"],
                 output_name="joined"),
            Step("aggregate", "Sum signed amounts by region", "aggregate",
                 {"input": "joined", "group_by": "region", "value_columns": ["amount"],
                  "name": "sales_by_region"}, ["join"], output_name="sales_by_region"),
            Step("chart", "Chart regional totals", "chart",
                 {"input": "sales_by_region", "x": "region", "y": "amount",
                  "name": "region_chart"}, ["aggregate"], output_name="region_chart"),
            Step("report", "Report regional totals", "report",
                 {"inputs": ["sales_by_region", "region_chart"], "name": "final_report"}, ["aggregate", "chart"],
                 output_name="final_report"),
        ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, help="fresh directory for synthetic data and outputs")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=False)
    spec = prepare(args.directory)
    config = AgentConfig(data_root=str(args.directory), outputs_root=str(args.directory / "runs"))
    summary = Orchestrator(CrossSourcePlanner(), Replanner(), REGISTRY, StateManager.new_run, config).run(spec)
    print(summary.report_path or summary.trace_path)
    raise SystemExit(summary.status != "finished")
