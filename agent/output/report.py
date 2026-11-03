"""``report`` — assemble the final markdown analysis report.

The report is itself a tool so that it runs through the same pipeline as
every other step: planned, executed, verified, and recorded in the
execution trace with full provenance.

Sections:

  1. header (goal, run id, generation time)
  2. execution summary (per-step status / attempts / duration)
  3. data provenance (lineage chains pulled from the state manager)
  4. results (markdown tables, figures, embedded text)
  5. notes (failures / skips / recoveries, if any)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from agent.core.types import RunContext
from agent.tools.base import ArtifactSpec, Tool, register

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.types import Artifact


def _md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    def cell(v: Any) -> str:
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v).replace("|", "\\|")

    out = ["| " + " | ".join(headers) + " |"]
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    out.extend("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)
    return out


@register
class ReportTool(Tool):
    name = "report"
    description = (
        "Assemble the final markdown analysis report from artifact inputs: "
        "execution summary, provenance chains, result tables and figures."
    )
    required_args = {"inputs": list}
    optional_args = {"title": str}
    input_contract = {
        "inputs": "list of artifact names to include in the report",
        "name": "output artifact name (report file is written to the run root)",
        "title": "report title (optional, defaults to the goal)",
    }
    output_contract = {"kind": "text", "columns": "n/a — produces report.md"}
    artifact_args = ["inputs"]
    semantic_rules = []

    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        arts: list["Artifact"] = args["inputs"]
        name = str(args.get("name") or "final_report")
        title = str(args.get("title") or f"数据分析报告：{ctx.goal}")

        run_dir = Path(ctx.run_dir)
        lines: list[str] = [
            f"# {title}",
            "",
            f"- **目标**: {ctx.goal}",
            f"- **运行**: {run_dir.name}",
            f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        # ---- execution summary ------------------------------------------
        summaries = ctx.step_summaries()
        lines += ["## 执行摘要", ""]
        lines += _md_table(
            ["步骤", "工具", "状态", "尝试", "耗时(ms)", "错误"],
            [
                [
                    r["step_id"],
                    r["tool"],
                    r["status"],
                    r["attempts"],
                    r["duration_ms"],
                    (r["error"] or "")[:60],
                ]
                for r in summaries
            ],
        )
        if any(r["status"] == "running" for r in summaries):
            lines += [
                "",
                "> 注: 状态为 `running` 的行即本报告步骤自身——"
                "报告内容生成时它尚未完成落盘。",
            ]
        lines.append("")

        # ---- provenance ----------------------------------------------------
        lines += ["## 数据溯源", ""]
        for art in arts:
            for line in ctx.provenance_lines(art.name):
                lines.append(f"- {line}")
        lines.append("")

        if ctx.rule_citations:
            lines += ["## 业务规则来源", ""]
            for citation in ctx.rule_citations:
                lines += [f"- {citation['rule']} ({citation['source']}:{citation['line']})",
                          f"  > {citation['quote']}"]
            lines.append("")

        # ---- results ---------------------------------------------------------
        lines += ["## 结果", ""]
        for art in arts:
            if art.kind in ("dataframe", "table"):
                df = pd.read_csv(run_dir / art.path)
                lines += [f"### {art.name}", "",
                          f"共 {len(df)} 行（最多展示 20 行），完整数据: `{art.path}`", ""]
                show = df.head(20)
                lines += _md_table(list(show.columns), show.values.tolist())
                lines.append("")
            elif art.kind == "figure":
                lines += [f"### {art.name}", "", f"![{art.name}]({art.path})", ""]
            elif art.kind == "text" and Path(run_dir / art.path).is_file():
                content = (run_dir / art.path).read_text(encoding="utf-8")
                lines += [f"### {art.name}", "", content, ""]

        lines += ["## 验证与限制", ""]
        for event in ctx.verification_events:
            status = "通过" if event["ok"] else "失败"
            checks = event.get("checks", [])
            detail = ", ".join(check["name"] for check in checks)
            if not checks:
                status = "未覆盖"
            lines.append(f"- {event['step_id']}（尝试 {event['attempt']}） {status} {detail}")
        lines += ["", "验证范围以列出的检查为准，未配置独立任务契约时，业务口径仍需结合数据和来源复核。", ""]

        # ---- notes -------------------------------------------------------------
        bad = [r for r in summaries if r["status"] in ("failed", "skipped")]
        recovered = [r for r in summaries if r["status"] == "recovered" or r["attempts"] > 1]
        if recovered:
            lines += ["## 备注", "", "**恢复事件**（失败后经重试/重规划恢复）:", ""]
            lines += _md_table(
                ["步骤", "工具", "尝试次数"],
                [[r["step_id"], r["tool"], r["attempts"]] for r in recovered],
            )
            lines.append("")
        if bad:
            lines += ["## 警告", "", "以下步骤未成功完成，结果可能不完整:", ""]
            lines += _md_table(
                ["步骤", "工具", "状态", "错误"],
                [[r["step_id"], r["tool"], r["status"], r["error"] or ""] for r in bad],
            )
            lines.append("")

        out = run_dir / "report.md"
        out.write_text("\n".join(lines), encoding="utf-8")

        return [
            ArtifactSpec(
                name=name,
                kind="text",
                path="report.md",
                meta={
                    "n_input_artifacts": len(arts),
                    "n_steps": len(summaries),
                    "n_failed_or_skipped": len(bad),
                    "n_recovered": len(recovered),
                },
            )
        ]
