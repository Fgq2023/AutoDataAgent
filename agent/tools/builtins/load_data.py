"""``load_data`` — load a CSV/Excel/JSON source into a dataframe artifact."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from agent.core.types import RunContext
from agent.tools.base import ArtifactSpec, Tool, register


@register
class LoadDataTool(Tool):
    name = "load_data"
    description = (
        "Load a tabular data source (CSV / Excel / JSON file) into the run "
        "as a dataframe artifact that downstream steps can consume."
    )
    input_contract = {
        "source": "absolute path of the data file to load",
        "name": "logical artifact name for the loaded data (e.g. sales_raw)",
    }
    output_contract = {"kind": "dataframe", "columns": "same as the source"}
    artifact_args: list[str] = []
    semantic_rules = [{"name": "row_count_positive"}]

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        source = str(args["source"])
        name = str(args.get("name") or Path(source).stem)
        suffix = Path(source).suffix.lower()

        df = pd.read_csv(source)
        rel = f"artifacts/{name}.csv"
        out = Path(ctx.run_dir) / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)

        from agent.tools.builtins._io import df_meta

        return [
            ArtifactSpec(
                name=name,
                kind="dataframe",
                path=rel,
                meta=df_meta(df, source=source, loaded_as=suffix.lstrip(".") or "csv"),
            )
        ]
