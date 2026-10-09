"""``aggregate`` / ``aggregate_pivot`` — two equivalent aggregation tools.

``aggregate_pivot`` exists as the *replanning alternative* to
``aggregate``: when the recovery policy decides REPLAN for a failing
aggregation step, the replanner (subtopic A) swaps the tool, keeping the
same input contract.  This demonstrates dynamic replanning end-to-end.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agent.core.types import RunContext
from agent.tools.base import ArtifactSpec, Tool, register
from agent.tools.builtins._io import read_df, write_df_artifact


def _derive_group_key(df: pd.DataFrame, group_by: str, date_column: str | None) -> pd.Series | None:
    """Resolve the grouping key: a plain column, or 'month' from a date column."""
    if group_by == "month":
        col = date_column or next(
            (c for c in df.columns if "date" in str(c).lower() or "日期" in str(c)), None
        )
        if col is None or col not in df.columns:
            return None
        key = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m")
        return key
    if group_by in df.columns:
        return df[group_by]
    return None


def _numeric_value_columns(df: pd.DataFrame, value_columns: list[str]) -> list[str]:
    numeric = set(df.select_dtypes("number").columns)
    return [c for c in value_columns if c in numeric]


class _BaseAggregate(Tool):
    input_contract = {
        "input": "artifact name of the dataframe to aggregate",
        "name": "output artifact name",
        "group_by": "grouping key: a column name, or 'month' to derive YYYY-MM from date_column",
        "date_column": "column holding dates (used when group_by == 'month'), optional",
        "value_columns": "numeric columns to sum per group",
    }
    output_contract = {
        "kind": "dataframe",
        "columns": "group_by column + one summed column per value column",
    }
    artifact_args = ["input"]
    semantic_rules = [
        {"name": "no_row_growth", "input_arg": "input"},
        {"name": "row_count_positive"},
    ]

    def _value_columns(self, df: pd.DataFrame, args: dict[str, Any]) -> list[str]:
        requested = list(args.get("value_columns") or [])
        if requested:
            return _numeric_value_columns(df, requested)
        # default: every numeric column except unit-price-like columns,
        # whose sums are meaningless
        return [
            c
            for c in df.select_dtypes("number").columns
            if "price" not in str(c).lower() and "单价" not in str(c)
        ]

    def _common(self, args: dict[str, Any], ctx: RunContext) -> tuple[Any, str, pd.DataFrame, list[str]]:
        art = args["input"]
        name = str(args.get("name") or f"{art.name}_agg")
        df = read_df(ctx.run_dir, art)
        group_by = str(args.get("group_by") or "month")
        value_columns = self._value_columns(df, args)
        return art, name, df, value_columns


@register
class AggregateTool(_BaseAggregate):
    name = "aggregate"
    description = "Aggregate a dataframe with groupby(...).sum() (primary implementation)."

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        art, name, df, value_columns = self._common(args, ctx)
        group_by = str(args.get("group_by") or "month")

        key = _derive_group_key(df, group_by, args.get("date_column"))
        if key is None:
            raise ValueError(
                f"cannot derive group key {group_by!r} from columns {list(df.columns)}"
            )
        if not value_columns:
            raise ValueError("no numeric value columns found to aggregate")

        work = df.copy()
        work["_group"] = key
        work = work.dropna(subset=["_group"])
        out = work.groupby("_group", as_index=False)[value_columns].sum()
        out = out.rename(columns={"_group": group_by})
        out = out.sort_values(group_by).reset_index(drop=True)

        spec = write_df_artifact(
            ctx.run_dir, name, out,
            group_by=group_by,
            value_columns=value_columns,
            input_artifact=art.artifact_id,
        )
        return [spec]


@register
class AggregatePivotTool(_BaseAggregate):
    name = "aggregate_pivot"
    description = (
        "Aggregate a dataframe with pivot_table(..., aggfunc='sum') — the "
        "replanning alternative to `aggregate` with an identical contract."
    )

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        art, name, df, value_columns = self._common(args, ctx)
        group_by = str(args.get("group_by") or "month")

        key = _derive_group_key(df, group_by, args.get("date_column"))
        if key is None:
            raise ValueError(
                f"cannot derive group key {group_by!r} from columns {list(df.columns)}"
            )
        if not value_columns:
            raise ValueError("no numeric value columns found to aggregate")

        work = df.copy()
        work["_group"] = key
        work = work.dropna(subset=["_group"])
        out = work.pivot_table(
            index="_group", values=value_columns, aggfunc="sum"
        ).reset_index()
        out = out.rename(columns={"_group": group_by})
        out = out[[group_by] + value_columns]  # pivot_table reorders; keep contract
        out = out.sort_values(group_by).reset_index(drop=True)

        spec = write_df_artifact(
            ctx.run_dir, name, out,
            group_by=group_by,
            value_columns=value_columns,
            input_artifact=art.artifact_id,
            implementation="pivot_table",
        )
        return [spec]
