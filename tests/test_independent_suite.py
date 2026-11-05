import importlib.util
import json
import sqlite3
from decimal import Decimal
from pathlib import Path


def builder():
    path = Path(__file__).resolve().parents[1] / "scripts/prepare_independent_suite.py"
    spec = importlib.util.spec_from_file_location("independent_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task_split_is_domain_disjoint_and_ids_are_unique():
    root = Path(__file__).resolve().parents[1]
    tasks = [json.loads(line) for line in (root / "benchmarks/independent_v1/tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(tasks) == len({t["task_id"] for t in tasks}) == 30
    domains = {split: {t["domain"] for t in tasks if t["split"] == split} for split in ("dev", "validation", "test")}
    assert domains == {"dev": {"bike"}, "validation": {"wine"}, "test": {"energy"}}
    assert [sum(t["split"] == s for t in tasks) for s in domains] == [12, 8, 10]
    assert all("answer" not in t and "contract" not in t for t in tasks)


def test_independent_sql_reference_matches_hand_calculated_answer(tmp_path):
    module = builder()
    dbpath = tmp_path / "tiny.db"
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE events(date TEXT, score REAL)")
        db.executemany("INSERT INTO events VALUES (?,?)", [("2020-01-01", 2), ("2020-01-03", 4), ("2021-01-01", 100)])
    contract = {"group_by": [module.g("date", "month", "month")],
                "metrics": [module.m("sum", "score"), module.m("mean", "score"), module.m("count")],
                "filters": [module.f("date", "lt", "2021-01-01", "text")]}
    answer = module.sql_reference(contract, dbpath, "events")
    assert answer == {("2020-01",): {"sum_score": Decimal(6), "mean_score": Decimal(3), "n_rows": Decimal(2)}}


def test_batch_budget_counts_failed_requests(monkeypatch):
    import pytest
    from urllib.error import URLError
    from scripts.run_independent_suite import CountedClient, RequestBudgetExceeded

    def fail(*args, **kwargs):
        raise URLError("provider timeout")

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr("urllib.request.urlopen", fail)
    monkeypatch.setattr("time.sleep", lambda delay: None)
    client = CountedClient(1)
    with pytest.raises(RequestBudgetExceeded):
        client.complete("system", "user")
    assert client.total == client.request_count == 1
    assert len(client.calls) == 1
    client.calls.clear()
    with pytest.raises(RequestBudgetExceeded):
        client.complete("system", "next")
    assert client.total == 1
    assert client.calls == []
