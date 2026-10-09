"""``sql_query`` — read-only SELECT execution against a SQLite database."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from agent.core.types import RunContext
from agent.tools.base import ArtifactSpec, Tool, register
from agent.tools.builtins._io import df_meta

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)


def assert_read_only(query: str) -> None:
    """Guard: only a single SELECT statement is allowed."""
    q = query.strip().rstrip(";").strip()
    if not q.lower().startswith(("select", "with")):
        raise ValueError("only SELECT/WITH queries are allowed in sql_query")
    if ";" in q:
        raise ValueError("multiple statements are not allowed in sql_query")
    if _FORBIDDEN.search(q):
        raise ValueError("sql_query refuses mutation keywords")


@register
class SqlQueryTool(Tool):
    name = "sql_query"
    description = (
        "Execute a read-only SELECT query against a SQLite database file "
        "and store the result as a table artifact."
    )
    input_contract = {
        "source": "absolute path of the .db file",
        "query": "a single SELECT statement",
        "name": "output artifact name",
    }
    output_contract = {"kind": "table", "columns": "whatever the query returns"}
    artifact_args: list[str] = []
    semantic_rules = [{"name": "row_count_positive"}]

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        source = str(args["source"])
        query = str(args["query"])
        name = str(args.get("name") or "sql_result")

        assert_read_only(query)

        if not Path(source).is_file():
            raise FileNotFoundError(f"database not found: {source}")

        conn = sqlite3.connect(source)
        try:
            df = pd.read_sql_query(query, conn)
        finally:
            conn.close()

        rel = f"artifacts/{name}.csv"
        out = Path(ctx.run_dir) / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)

        return [
            ArtifactSpec(
                name=name,
                kind="table",
                path=rel,
                meta=df_meta(df, source=source, query=query),
            )
        ]
