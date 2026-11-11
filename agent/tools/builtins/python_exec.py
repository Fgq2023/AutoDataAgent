"""``python_exec`` — run a generated pandas snippet inside the sandbox."""

from __future__ import annotations
from agent.tools.builtins._io import output_path

from pathlib import Path
from typing import Any

import pandas as pd
import json

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
    required_args = {"input": str, "code": str}
    optional_args = {}
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
        output_csv = output_path(ctx.run_dir, name, ".csv")

        run_isolated(code, input_csv, output_csv, timeout_s=float(ctx.timeout_s),
                     backend=ctx.sandbox_backend, image=ctx.sandbox_image, input_meta=art.meta)

        from agent.tools.builtins._io import read_table
        schema = json.loads(Path(str(output_csv) + ".schema.json").read_text(encoding="utf-8"))
        df = read_table(output_csv, schema)
        return [
            ArtifactSpec(
                name=name,
                kind="dataframe",
                path=rel,
                meta=df_meta(df, sandboxed=True, code=code),
            )
        ]
