import csv
import json
import sqlite3
from pathlib import Path

import pytest

from agent.core.types import Artifact
from agent.verification.task import validate_contracts, check_contract, load_contracts
from agent.verification.tabular import evaluate


@pytest.fixture(params=["csv", "sqlite"])
def sample(request, tmp_path):
    records = [("2024-01-10", "A", 10), ("2024-01-15", "A", 30), ("2024-02-10", "B", 8), ("2025-01-10", "A", 999)]
    source = tmp_path / ("events.csv" if request.param == "csv" else "events.db")
    if request.param == "csv":
        with source.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["ts", "category", "score"])
            writer.writerows(records)
    else:
        with sqlite3.connect(source) as db:
            db.execute("CREATE TABLE events(ts TEXT, category TEXT, score REAL)")
            db.executemany("INSERT INTO events VALUES (?,?,?)", records)
    contract = {"kind": "tabular_aggregate", "artifact": "result", "source": str(source),
                "source_type": request.param, "table": "events",
                "filters": [{"column": "ts", "op": "gte", "value": "2024-01-01", "type": "text"},
                            {"column": "ts", "op": "lt", "value": "2025-01-01", "type": "text"}],
                "group_by": [{"column": "ts", "transform": "month", "output": "month"}],
                "metrics": [{"op": "sum", "column": "score", "output": "total"},
                            {"op": "count", "output": "n"},
                            {"op": "mean", "column": "score", "output": "average"}]}
    validate_contracts([contract])
    return contract, tmp_path


def write_result(root, records):
    with (root / "result.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["month", "total", "n", "average"])
        writer.writerows(records)
    return Artifact("result", "result", "table", "result.csv")


def test_generic_csv_and_sqlite_answers(sample):
    c, root = sample
    art = write_result(root, [["2024-01", 40, 2, 20], ["2024-02", 8, 1, 8]])
    assert check_contract(c, art, root).passed


@pytest.mark.parametrize("records", [
    [["2025-01", 999, 1, 999]],
    [["2024-01", 20, 2, 20], ["2024-02", 8, 1, 8]],
    [["2024", 48, 3, 16]],
    [["2024-01", 40, "2.001", 20], ["2024-02", 8, 1, 8]],
    [["2024-01", "NaN", 2, 20], ["2024-02", 8, 1, 8]],
    [["2024-01", 40, 2, 20], ["2024-01", 40, 2, 20]],
])
def test_wrong_answers_are_rejected(sample, records):
    c, root = sample
    assert not check_contract(c, write_result(root, records), root).passed


def test_numeric_filter_and_identity_grouping(sample):
    c, root = sample
    c["filters"] = [{"column": "score", "op": "gt", "value": 20, "type": "number"}]
    c["group_by"] = [{"column": "category", "output": "category"}]
    result = evaluate(c)
    assert result[("A",)]["total"] == 1029
    assert result[("A",)]["n"] == 2


def test_global_aggregate(sample):
    c, root = sample
    c["group_by"] = []
    assert evaluate(c)[()]["total"] == 48


def test_unknown_contract_fields_fail_closed(sample):
    c, root = sample
    c["filter"] = []  # misspelling must not be silently ignored
    with pytest.raises(ValueError, match="unknown"):
        validate_contracts([c])


def test_relative_source_is_based_on_contract_file(sample):
    c, root = sample
    c["source"] = Path(c["source"]).name
    path = root / "contract.json"
    path.write_text(json.dumps([c]), encoding="utf-8")
    loaded = load_contracts(path)
    assert Path(loaded[0]["source"]).parent == root
