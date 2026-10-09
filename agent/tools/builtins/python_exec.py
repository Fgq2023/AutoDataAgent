"""``python_exec`` — run a generated pandas snippet inside the sandbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from agent.core.types import RunContext
from agent.tools.base import ArtifactSpec, Tool, register
from agent.tools.builtins._io import df_meta
from agent.tools.sandbox import run_isolated


@register
class PythonExecTool(Tool):
    name = "python_exec"
    description = (
        "Run a short pandas snippet in an isolated subprocess. The snippet "
        "receives the input dataframe as `df` and must bind its output "
        "DataFrame to `result`."
    )
    input_contract = {
        "input": "artifact name of the input dataframe",
        "code": "python code; `df` is the input, bind `result` (DataFrame)",
        "name": "output artifact name",
    }
    output_contract = {"kind": "dataframe", "columns": "whatever the code produces"}
    artifact_args = ["input"]
    semantic_rules = []

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        art = args["input"]
        code = str(args["code"])
        name = str(args.get("name") or f"{art.name}_exec")

        run_dir = Path(ctx.run_dir)
        input_csv = run_dir / art.path
        rel = f"artifacts/{name}.csv"
        output_csv = run_dir / rel

        run_isolated(code, input_csv, output_csv, timeout_s=float(ctx.timeout_s))

        df = pd.read_csv(output_csv)
        return [
            ArtifactSpec(
                name=name,
                kind="dataframe",
                path=rel,
                meta=df_meta(df, sandboxed=True, code=code),
            )
        ]
