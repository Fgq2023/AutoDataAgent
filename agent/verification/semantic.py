"""Semantic-layer verification (subtopic C).

Rules are *declared by each tool* (``Tool.semantic_rules``) and executed
here against the artifacts of a finished step attempt.  Rules may inspect
the input artifacts too, so they can catch "the output is well-formed but
makes no sense given the input" failures that schema checks cannot.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from agent.core.types import CheckResult

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.types import Artifact


def _load(run_dir: str | Path, art: "Artifact") -> pd.DataFrame:
    return pd.read_csv(Path(run_dir) / art.path)


# --------------------------------------------------------------------------- #
# Rule implementations — each takes (output, inputs, params, run_dir)
# --------------------------------------------------------------------------- #
def row_count_positive(out: "Artifact", inputs: list["Artifact"],
                       params: dict[str, Any], run_dir: str | Path) -> CheckResult:
    label = f"semantic:row_count_positive:{out.artifact_id}"
    n = out.meta.get("n_rows")
    if n is None:
        try:
            n = len(_load(run_dir, out))
        except Exception as exc:  # noqa: BLE001
            return CheckResult(label, False, f"cannot load output: {exc}")
    ok = int(n) > 0
    return CheckResult(label, ok, f"n_rows={n}")


def no_row_growth(out: "Artifact", inputs: list["Artifact"],
                  params: dict[str, Any], run_dir: str | Path) -> CheckResult:
    label = f"semantic:no_row_growth:{out.artifact_id}"
    if not inputs:
        return CheckResult(label, True, "no input to compare — skipped")
    try:
        n_out = out.meta.get("n_rows")
        if n_out is None:
            n_out = len(_load(run_dir, out))
        n_in = max(
            int(a.meta.get("n_rows", 0) or 0) or len(_load(run_dir, a))
            for a in inputs
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(label, False, f"cannot load artifacts: {exc}")
    ok = int(n_out) <= int(n_in)
    return CheckResult(label, ok, f"n_out={n_out} <= n_in={n_in}")


def non_negative(out: "Artifact", inputs: list["Artifact"],
                 params: dict[str, Any], run_dir: str | Path) -> CheckResult:
    """All values of ``params['columns']`` (or every numeric column) >= 0."""
    label = f"semantic:non_negative:{out.artifact_id}"
    try:
        df = _load(run_dir, out)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(label, False, f"cannot load output: {exc}")
    cols = [c for c in (params.get("columns") or []) if c in df.columns]
    if not cols:
        cols = list(df.select_dtypes("number").columns)
    if not cols:
        return CheckResult(label, True, "no numeric columns — skipped")
    bad = {c: int((df[c] < 0).sum()) for c in cols if (df[c] < 0).any()}
    ok = not bad
    detail = "all non-negative" if ok else f"negative values: {bad}"
    return CheckResult(label, ok, detail)


def figure_nonempty(out: "Artifact", inputs: list["Artifact"],
                    params: dict[str, Any], run_dir: str | Path) -> CheckResult:
    label = f"semantic:figure_nonempty:{out.artifact_id}"
    path = Path(run_dir) / out.path
    ok = path.is_file() and path.stat().st_size > 1024  # a real plot, not a stub
    size = path.stat().st_size if path.is_file() else 0
    return CheckResult(label, ok, f"{size} bytes")


RULES = {
    "row_count_positive": row_count_positive,
    "no_row_growth": no_row_growth,
    "non_negative": non_negative,
    "figure_nonempty": figure_nonempty,
}


def run_rules(rules: list[dict[str, Any]], out: "Artifact",
              inputs: list["Artifact"], run_dir: str | Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for rule in rules:
        fn = RULES.get(str(rule.get("name")))
        if fn is None:
            results.append(
                CheckResult(f"semantic:unknown:{rule.get('name')}", False,
                            "rule not implemented")
            )
            continue
        params = dict(rule.get("params") or {})
        input_arg = rule.get("input_arg")
        rule_inputs = inputs
        if input_arg is not None:
            # tools declare which artifact arg feeds the rule; the verifier
            # passes *all* resolved inputs unless narrowed further
            rule_inputs = inputs
        results.append(fn(out, rule_inputs, params, run_dir))
    return results
