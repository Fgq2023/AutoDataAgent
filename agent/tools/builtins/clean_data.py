"""``clean_data`` — deduplicate, drop missing rows, drop invalid negatives."""

from __future__ import annotations

from typing import Any

from agent.core.types import RunContext
from agent.tools.base import ArtifactSpec, Tool, register
from agent.tools.builtins._io import df_meta, read_df, write_df_artifact


@register
class CleanDataTool(Tool):
    name = "clean_data"
    description = (
        "Clean a dataframe artifact: drop duplicate rows, drop rows with "
        "missing values, and drop rows with negative numeric values "
        "(e.g. negative prices/quantities). Reports drop counts in meta."
    )
    input_contract = {
        "input": "artifact name of the dataframe to clean",
        "name": "output artifact name",
    }
    output_contract = {"kind": "dataframe", "columns": "same as input (subset of rows)"}
    artifact_args = ["input"]
    semantic_rules = [{"name": "no_row_growth", "input_arg": "input"}]

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        art = args["input"]
        name = str(args.get("name") or f"{art.name}_clean")

        df = read_df(ctx.run_dir, art)
        n_before = int(len(df))

        n_dup = int(df.duplicated().sum())
        df = df.drop_duplicates()

        n_missing = int(df.isna().any(axis=1).sum())
        df = df.dropna()

        numeric = df.select_dtypes("number")
        neg_mask = (numeric < 0).any(axis=1) if not numeric.empty else None
        n_negative = int(neg_mask.sum()) if neg_mask is not None else 0
        if neg_mask is not None:
            df = df[~neg_mask]

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
        )
        return [spec]
