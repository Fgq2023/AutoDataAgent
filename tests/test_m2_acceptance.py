"""Additional synthetic acceptance cases for milestone M2."""
import csv
from pathlib import Path

from agent.core.task_spec import TaskSpec
from agent.core.types import Step, TaskGraph


def run_case(orchestrator, data_root, goal, sources, steps, artifact, columns):
    class FixedPlanner:
        label = "offline-m2-acceptance"

        def plan(self, goal, ctx):
            for step in steps:
                if "source" in step.args:
                    step.args["source"] = str(data_root / step.args["source"])
            return TaskGraph(goal, steps)

    orchestrator.planner = FixedPlanner()
    result = orchestrator.run(TaskSpec(goal, sources=sources, output_columns={artifact: columns}))
    assert result.status == "finished" and result.report_path
    item = next(item for item in result.artifacts if item["name"] == artifact)
    with (Path(result.run_dir) / item["path"]).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_cleaning_invalid_dates_and_signed_refunds(orchestrator, data_root):
    (data_root / "refunds.csv").write_text("date,amount\n2024-01-01,-5\nbad,20\n2024-01-01,-5\n2024-01-02,\n", encoding="utf-8")
    rows = run_case(orchestrator, data_root, "Clean refunds", ["refunds.csv"], [
        Step("raw", "Read CSV", "load_data", {"source": "refunds.csv", "name": "raw"}, output_name="raw"),
        Step("clean", "Remove invalid dates and missing or duplicate rows", "clean_data",
             {"input": "raw", "name": "clean", "date_column": "date", "date_format": "%Y-%m-%d"},
             ["raw"], output_name="clean"),
        Step("report", "Report the clean rows", "report", {"inputs": ["clean"]}, ["clean"], output_name="final_report"),
    ], "clean", ["date", "amount"])
    assert len(rows) == 1 and rows[0]["date"] == "2024-01-01" and float(rows[0]["amount"]) == -5


def test_table_qa_filters_and_counts_json(orchestrator, data_root):
    (data_root / "records.json").write_text('[{"state":"ok","amount":4},{"state":"bad","amount":2},{"state":"ok","amount":6}]', encoding="utf-8")
    rows = run_case(orchestrator, data_root, "Count and sum accepted records", ["records.json"], [
        Step("raw", "Read records", "load_data", {"source": "records.json", "name": "raw"}, output_name="raw"),
        Step("answer", "Count and sum", "python_exec",
             {"input": "raw", "name": "answer",
              "code": "ok = df.loc[df.state == 'ok', 'amount']\nresult = pd.DataFrame({'n': [len(ok)], 'total': [ok.sum()]})"},
             ["raw"], output_name="answer"),
        Step("report", "Report totals", "report", {"inputs": ["answer"]}, ["answer"], output_name="final_report"),
    ], "answer", ["n", "total"])
    assert rows == [{"n": "2", "total": "10"}]


def test_sql_year_boundary_by_city(orchestrator, data_root):
    rows = run_case(orchestrator, data_root, "Sum 2025 orders by city", ["orders.db"], [
        Step("answer", "Join and aggregate", "sql_query",
             {"source": "orders.db", "name": "answer",
              "query": "SELECT city, SUM(amount) AS total FROM orders JOIN customers ON customers.id=orders.customer_id WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY city"},
             output_name="answer"),
        Step("report", "Report city totals", "report", {"inputs": ["answer"]}, ["answer"], output_name="final_report"),
    ], "answer", ["city", "total"])
    assert len(rows) == 1 and rows[0]["city"] == "gz" and float(rows[0]["total"]) == 500
