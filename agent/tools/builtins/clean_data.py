"""``clean_data`` — apply explicit tabular cleaning rules."""

from __future__ import annotations

from typing import Any

from agent.core.types import RunContext
from agent.tools.base import ArtifactSpec, Tool, register
from agent.tools.builtins._io import df_meta, read_df, write_df_artifact


@register
class CleanDataTool(Tool):
    name = "clean_data"
    description = (
        "Clean a dataframe and record removal counts. Negative values are preserved by default."
    )
    required_args = {"input": str}
    optional_args = {"drop_duplicates": bool, "drop_missing": bool, "drop_negative": bool, "date_column": str, "date_format": str}
    input_contract = {
        "input": "artifact name of the dataframe to clean",
        "name": "output artifact name",
        "drop_duplicates": "boolean, default true",
        "drop_missing": "boolean, default true",
        "drop_negative": "boolean, default false; use only if all negative numeric values are invalid",
        "date_column": "optional date column to validate",
        "date_format": "explicit date format, required with date_column",
    }
    output_contract = {"kind": "dataframe", "columns": "same as input (subset of rows)"}
    artifact_args = ["input"]
    semantic_rules = [{"name": "no_row_growth", "input_arg": "input"}]

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        art = args["input"]
        name = str(args.get("name") or f"{art.name}_clean")

        df = read_df(ctx.run_dir, art)
        n_before = int(len(df))

        import pandas as pd

        policies = {key: args.get(key, default) for key, default in (
            ("drop_duplicates", True), ("drop_missing", True), ("drop_negative", False)
        )}
        if not all(isinstance(value, bool) for value in policies.values()):
            raise ValueError("cleaning policy flags must be boolean")
        n_dup = int(df.duplicated().sum()) if policies["drop_duplicates"] else 0
        if policies["drop_duplicates"]:
            df = df.drop_duplicates()

        n_missing = int(df.isna().any(axis=1).sum()) if policies["drop_missing"] else 0
        if policies["drop_missing"]:
            df = df.dropna()

        n_negative = 0
        if policies["drop_negative"]:
            neg_mask = (df.select_dtypes("number") < 0).any(axis=1)
            n_negative = int(neg_mask.sum())
            df = df[~neg_mask]

        n_invalid_dates = 0
        if "date_column" in args or "date_format" in args:
            column, date_format = args.get("date_column"), args.get("date_format")
            if not isinstance(column, str) or not isinstance(date_format, str) or not date_format:
                raise ValueError("date_column and date_format must be provided together")
            parsed = pd.to_datetime(df[column], format=date_format, errors="coerce")
            invalid = df[column].notna() & parsed.isna()
            n_invalid_dates = int(invalid.sum())
            df = df[~invalid]

        n_after = int(len(df))
        if n_after == 0:
            raise ValueError("cleaning removed every row — refusing to produce an empty artifact")

        spec = write_df_artifact(
            ctx.run_dir, name, df,
            n_rows_before=n_before,
            n_rows_after=n_after,
            dropped_duplicates=n_dup,
            dropped_missing=n_missing,
            dropped_negative=n_negative,
            dropped_invalid_dates=n_invalid_dates,
        )
        return [spec]
