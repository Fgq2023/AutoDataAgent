"""Shared I/O helpers for builtin tools (subtopic C, internal)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from agent.tools.base import ArtifactSpec

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.types import Artifact


def artifact_path(run_dir: str | Path, art: "Artifact") -> Path:
    """Absolute filesystem path of an artifact."""
    return Path(run_dir) / art.path


def read_df(run_dir: str | Path, art: "Artifact") -> pd.DataFrame:
    return pd.read_csv(artifact_path(run_dir, art))


def df_meta(df: pd.DataFrame, **extra: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "columns": [{"name": str(c), "dtype": str(t)} for c, t in df.dtypes.items()],
        "n_rows": int(len(df)),
    }
    meta.update(extra)
    return meta


def write_df_artifact(run_dir: str | Path, name: str, df: pd.DataFrame,
                      **extra: Any) -> ArtifactSpec:
    """Write a DataFrame as the run artifact ``artifacts/<name>.csv``."""
    rel = f"artifacts/{name}.csv"
    path = Path(run_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return ArtifactSpec(name=name, kind="dataframe", path=rel, meta=df_meta(df, **extra))
