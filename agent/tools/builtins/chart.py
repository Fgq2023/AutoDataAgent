"""``chart`` — render a line chart of a dataframe artifact with matplotlib."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless rendering, must run before pyplot import

import matplotlib.pyplot as plt  # noqa: E402

from agent.core.types import RunContext
from agent.tools.base import ArtifactSpec, Tool, register
from agent.tools.builtins._io import read_df


@register
class ChartTool(Tool):
    name = "chart"
    description = (
        "Render a line chart of one dataframe artifact: x column vs y "
        "column, saved as a PNG figure artifact."
    )
    input_contract = {
        "input": "artifact name of the dataframe to plot",
        "x": "x column",
        "y": "y column",
        "title": "chart title (optional)",
        "name": "output artifact name",
    }
    output_contract = {"kind": "figure", "columns": "n/a — produces a PNG file"}
    artifact_args = ["input"]
    semantic_rules = [{"name": "figure_nonempty"}]

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        art = args["input"]
        x = str(args["x"])
        y = str(args["y"])
        title = str(args.get("title") or f"{y} by {x}")
        name = str(args.get("name") or f"{art.name}_chart")

        df = read_df(ctx.run_dir, art)
        missing = [c for c in (x, y) if c not in df.columns]
        if missing:
            raise ValueError(f"chart columns {missing} not in {list(df.columns)}")

        fig, ax = plt.subplots(figsize=(10, 5), dpi=140)
        ax.plot(df[x].astype(str), df[y], marker="o", linewidth=1.8)
        ax.set_title(title)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        if len(df) > 8:  # dense categorical x-axis: thin out the ticks
            step = max(1, len(df) // 10)
            ax.set_xticks(range(0, len(df), step))
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate(rotation=45)
        fig.tight_layout()

        rel = f"artifacts/{name}.png"
        out = Path(ctx.run_dir) / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out)
        plt.close(fig)

        return [
            ArtifactSpec(
                name=name,
                kind="figure",
                path=rel,
                meta={"x": x, "y": y, "title": title, "n_points": int(len(df))},
            )
        ]
