"""Shared I/O helpers for builtin tools (subtopic C, internal)."""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

import pandas as pd

from agent.tools.base import ArtifactSpec

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.types import Artifact


def artifact_path(run_dir: str | Path, art: "Artifact") -> Path:
    """Absolute filesystem path of an artifact."""
    root = Path(run_dir).resolve()
    path = (root / art.path).resolve()
    path.relative_to(root)
    return path


def read_df(run_dir: str | Path, art: "Artifact") -> pd.DataFrame:
    return read_table(artifact_path(run_dir, art), art.meta)


def read_table(path, meta):
    strings = meta.get("string_columns", [])
    df = pd.read_csv(path, converters={column: str for column in strings})
    for column, rows in meta.get("null_cells", {}).items():
        for row in rows:
            df.at[row, column] = None
    return df


def output_path(run_dir, name, suffix):
    if not isinstance(name, str) or not re.fullmatch(r"[\w-]+", name):
        raise ValueError("artifact name must contain only letters, digits, underscores or hyphens")
    root = Path(run_dir).resolve()
    path = (root / "artifacts" / (name + suffix)).resolve()
    path.relative_to(root)
    return path


def df_meta(df: pd.DataFrame, **extra: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "columns": [{"name": str(c), "dtype": str(t)} for c, t in df.dtypes.items()],
        "n_rows": int(len(df)),
    }
    strings = [str(column) for column in df.columns if str(df[column].dtype) in ("object", "str", "string")]
    meta["string_columns"] = strings
    meta["null_cells"] = {column: [i for i, missing in enumerate(df[column].isna()) if missing]
                          for column in strings}
    meta.update(extra)
    return meta


def write_df_artifact(run_dir: str | Path, name: str, df: pd.DataFrame,
                      **extra: Any) -> ArtifactSpec:
    """Write a DataFrame as the run artifact ``artifacts/<name>.csv``."""
    rel = f"artifacts/{name}.csv"
    path = output_path(run_dir, name, ".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return ArtifactSpec(name=name, kind="dataframe", path=rel, meta=df_meta(df, **extra))
