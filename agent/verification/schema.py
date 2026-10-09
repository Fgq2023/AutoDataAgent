"""Schema-layer verification (subtopic C).

Checks that every output artifact of a step actually is what its tool
contract promised: the file exists, is non-empty, has the right kind, and
(for dataframes) loads back with the exact shape recorded in its metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from agent.core.types import CheckResult

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.types import Artifact

_PNG_MAGIC = b"\x89PNG"


def check_artifact(art: "Artifact", run_dir: str | Path,
                   expected_kind: str | None) -> CheckResult:
    """Run all schema checks for one artifact; a single CheckResult."""
    path = Path(run_dir) / art.path
    label = f"schema:{art.artifact_id}"
    details: list[str] = []

    if not path.is_file():
        return CheckResult(label, False, f"missing file: {path}")
    if path.stat().st_size == 0:
        return CheckResult(label, False, f"empty file: {path}")
    if expected_kind and art.kind != expected_kind:
        details.append(f"kind {art.kind!r} != contract {expected_kind!r}")

    if art.kind in ("dataframe", "table"):
        try:
            df = pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(label, False, f"unreadable csv: {exc}")
        if df.empty:
            details.append("csv has zero rows")
        meta_rows = art.meta.get("n_rows")
        if meta_rows is not None and int(meta_rows) != int(len(df)):
            details.append(
                f"row count drifted: meta says {meta_rows}, file has {len(df)}"
            )
        meta_cols = [c.get("name") for c in art.meta.get("columns", [])]
        if meta_cols and list(df.columns) != list(meta_cols):
            details.append("columns differ from recorded meta")
    elif art.kind == "figure":
        with path.open("rb") as fh:
            if fh.read(4) != _PNG_MAGIC:
                details.append("not a valid PNG file")
    elif art.kind == "text":
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            details.append("not valid UTF-8 text")

    if details:
        return CheckResult(label, False, "; ".join(details))
    return CheckResult(label, True, f"{art.kind} at {art.path} OK")
