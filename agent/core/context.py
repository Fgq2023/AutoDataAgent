"""Data-source profiling.

Before planning, the agent inspects every file under the configured data
root and builds a :class:`DataContext`: for each source, its schema
(columns, row count) and a first-pass list of quality issues (missing
values, duplicate rows, negative numerics).  The planner (subtopic A)
uses this context to ground its plans in the *actual* data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.core.types import DataContext, DataProfile

_SUPPORTED = {".csv": "csv", ".xlsx": "xlsx", ".json": "json", ".db": "sqlite", ".sqlite": "sqlite"}


def _profile_csv_xlsx(path: Path) -> DataProfile:
    import pandas as pd

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)  # requires openpyxl

    issues: list[str] = []
    n_missing = int(df.isna().sum().sum())
    if n_missing:
        issues.append(f"{n_missing} missing cell(s)")
    n_dup = int(df.duplicated().sum())
    if n_dup:
        issues.append(f"{n_dup} duplicate row(s)")
    numeric = df.select_dtypes("number")
    n_neg = int((numeric < 0).sum().sum())
    if n_neg:
        issues.append(f"{n_neg} negative numeric value(s)")

    schema: dict[str, Any] = {
        "columns": [
            {"name": str(c), "dtype": str(t)} for c, t in df.dtypes.items()
        ],
        "n_rows": int(len(df)),
    }
    return DataProfile(
        name=path.stem,
        path=str(path.resolve()),
        kind=path.suffix.lower().lstrip("."),
        schema=schema,
        issues=issues,
    )


def _profile_sqlite(path: Path) -> DataProfile:
    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        schema: dict[str, Any] = {"tables": {}}
        for t in tables:
            cols = [
                {"name": r[1], "dtype": r[2]}
                for r in conn.execute(f'PRAGMA table_info("{t}")')
            ]
            n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            schema["tables"][t] = {"columns": cols, "n_rows": int(n)}
        return DataProfile(
            name=path.stem,
            path=str(path.resolve()),
            kind="sqlite",
            schema=schema,
        )
    finally:
        conn.close()


def _profile_json(path: Path) -> DataProfile:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    n_rows = len(data) if isinstance(data, list) else 1
    schema = {"type": "list" if isinstance(data, list) else "dict", "n_rows": n_rows}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        schema["columns"] = list(data[0].keys())
    return DataProfile(
        name=path.stem,
        path=str(path.resolve()),
        kind="json",
        schema=schema,
    )


def profile_data_sources(data_root: str | Path) -> DataContext:
    """Scan *data_root* and profile every supported file found."""
    root = Path(data_root)
    profiles: list[DataProfile] = []
    if not root.is_dir():
        return DataContext(root=str(root.resolve() if root.exists() else root), profiles=[])

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        kind = _SUPPORTED.get(path.suffix.lower())
        if kind is None:
            continue
        if kind in ("csv", "xlsx"):
            try:
                profiles.append(_profile_csv_xlsx(path))
            except Exception as exc:  # noqa: BLE001 — profiling must never crash a run
                profiles.append(
                    DataProfile(
                        name=path.stem,
                        path=str(path.resolve()),
                        kind=kind,
                        schema={},
                        issues=[f"failed to profile: {exc}"],
                    )
                )
        elif kind == "sqlite":
            profiles.append(_profile_sqlite(path))
        elif kind == "json":
            profiles.append(_profile_json(path))
    return DataContext(root=str(root.resolve()), profiles=profiles)
