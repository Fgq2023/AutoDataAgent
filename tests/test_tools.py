"""Subtopic C tests: registry, builtin tools, sandbox, verification."""

from __future__ import annotations

import pytest

from agent.core.types import RunContext
from agent.tools.base import REGISTRY, get_tool
from agent.tools.builtins.sql_query import assert_read_only
from agent.tools.sandbox import run_isolated


# ---------------------------------------------------------------------- #
# Registry
# ---------------------------------------------------------------------- #
def test_builtin_tools_registered():
    expected = {
        "load_data", "clean_data", "aggregate", "aggregate_pivot",
        "python_exec", "sql_query", "chart", "report",
    }
    assert expected <= set(REGISTRY)


def test_get_tool_unknown_raises():
    with pytest.raises(KeyError):
        get_tool("no_such_tool")


# ---------------------------------------------------------------------- #
# Tools (via a tiny in-memory run context)
# ---------------------------------------------------------------------- #
@pytest.fixture()
def ctx(tmp_path) -> RunContext:
    import pandas as pd

    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir()
    df = pd.DataFrame(
        {
            "date": ["2024-01-15", "2024-01-15", "2024-02-15", "2024-03-15"],
            "region": ["e", "e", "w", "w"],
            "units": [10, 10, 5, 8],
            "price": [2.0, 2.0, 2.0, -1.0],
            "revenue": [20.0, 20.0, 10.0, -8.0],
        }
    )
    df.to_csv(arts_dir / "raw.csv", index=False)
    return RunContext(
        run_dir=str(tmp_path),
        goal="test",
        lookup=lambda name: None,
        step_summaries=lambda: [],
        provenance_lines=lambda name: [],
        timeout_s=30.0,
    )


class _FakeArtifact:
    def __init__(self, name, path):
        self.artifact_id = name
        self.name = name
        self.kind = "dataframe"
        self.path = path
        self.meta = {"n_rows": 4}


def test_clean_data_drops_dirty_rows(ctx):
    art = _FakeArtifact("raw", "artifacts/raw.csv")
    specs = get_tool("clean_data").run({"input": art, "name": "clean"}, ctx)
    import pandas as pd

    out = pd.read_csv(ctx.run_dir + "/artifacts/clean.csv")
    assert len(out) == 2  # duplicate + negative dropped, 4 -> 2
    assert (out["price"] >= 0).all()
    assert specs[0].meta["dropped_duplicates"] == 1
    assert specs[0].meta["dropped_negative"] == 1


def test_aggregate_and_pivot_agree(ctx):
    art = _FakeArtifact("raw", "artifacts/raw.csv")
    args = {
        "input": art, "name": "monthly", "group_by": "month",
        "date_column": "date", "value_columns": ["units", "revenue"],
    }
    spec_a = get_tool("aggregate").run(args, ctx)[0]
    args["name"] = "monthly_pivot"
    spec_b = get_tool("aggregate_pivot").run(args, ctx)[0]

    import pandas as pd

    a = pd.read_csv(ctx.run_dir + "/" + spec_a.path)
    b = pd.read_csv(ctx.run_dir + "/" + spec_b.path)
    assert list(a.columns) == list(b.columns) == ["month", "units", "revenue"]
    assert a["revenue"].tolist() == b["revenue"].tolist()
    assert a["month"].tolist() == sorted(a["month"].tolist())


def test_aggregate_raises_on_missing_group_column(ctx):
    art = _FakeArtifact("raw", "artifacts/raw.csv")
    with pytest.raises(ValueError):
        get_tool("aggregate").run(
            {"input": art, "name": "x", "group_by": "nope"}, ctx
        )


def test_chart_writes_png(ctx):
    import pandas as pd

    (ctx.run_dir + "/artifacts/monthly.csv")  # noqa: B018
    pd.DataFrame({"month": ["01", "02"], "revenue": [1.0, 2.0]}).to_csv(
        ctx.run_dir + "/artifacts/monthly.csv", index=False
    )
    art = _FakeArtifact("monthly", "artifacts/monthly.csv")
    spec = get_tool("chart").run(
        {"input": art, "x": "month", "y": "revenue", "name": "trend"}, ctx
    )[0]
    data = open(ctx.run_dir + "/" + spec.path, "rb").read(8)
    assert data[:4] == b"\x89PNG"


# ---------------------------------------------------------------------- #
# SQL guard
# ---------------------------------------------------------------------- #
def test_sql_guard_rejects_mutations():
    with pytest.raises(ValueError):
        assert_read_only("DROP TABLE customers")
    with pytest.raises(ValueError):
        assert_read_only("SELECT 1; DELETE FROM orders")
    assert_read_only("SELECT * FROM orders")  # fine


def test_sql_query_tool(data_root, ctx):
    import sqlite3

    db = str(data_root / "orders.db")
    conn = sqlite3.connect(db)
    conn.close()
    spec = get_tool("sql_query").run(
        {"source": db, "query": "SELECT name FROM customers", "name": "names"},
        ctx,
    )[0]
    import pandas as pd

    out = pd.read_csv(ctx.run_dir + "/artifacts/names.csv")
    assert len(out) == 2
    assert "query" in spec.meta


# ---------------------------------------------------------------------- #
# Sandbox
# ---------------------------------------------------------------------- #
def test_sandbox_runs_code_and_captures_errors(tmp_path):
    import pandas as pd

    inp = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    pd.DataFrame({"a": [1, 2, 3]}).to_csv(inp, index=False)

    stdout = run_isolated("result = df[df.a > 1]", inp, out, timeout_s=60)
    assert "sandbox: ok" in stdout
    assert pd.read_csv(out)["a"].tolist() == [2, 3]

    with pytest.raises(RuntimeError):
        run_isolated("result = df[df.nonexistent > 1]", inp, out, timeout_s=60)


def test_sandbox_timeout(tmp_path):
    import pandas as pd

    inp = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    pd.DataFrame({"a": [1]}).to_csv(inp, index=False)
    from agent.tools.sandbox import SandboxTimeout

    with pytest.raises(SandboxTimeout):
        run_isolated(
            "import time; time.sleep(5); result = df", inp, out, timeout_s=1
        )


def test_python_exec_tool_end_to_end(ctx):
    art = _FakeArtifact("raw", "artifacts/raw.csv")
    spec = get_tool("python_exec").run(
        {
            "input": art,
            "name": "executed",
            "code": "result = df[df.revenue >= 0]",
        },
        ctx,
    )[0]
    import pandas as pd

    out = pd.read_csv(ctx.run_dir + "/" + spec.path)
    assert len(out) == 3
    assert spec.meta["sandboxed"] is True
