"""Command-line entry point.

Examples::

    python -m agent "分析 data/sales.csv 的月度销售趋势，输出月度汇总表和趋势图"

    # offline (default without an API key): deterministic mock planner
    # with OPENAI_API_KEY set: LLM planner (falls back to mock on errors)

    # failure-injection demos:
    python -m agent "..." --inject-failure s3            # transient -> RETRY
    python -m agent "..." --inject-failure s3:permanent  # persistent -> REPLAN
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.core.config import load_config
from agent.core.orchestrator import Orchestrator
from agent.core.types import RunSummary
from agent.planning.llm_planner import LLMPlanner
from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import Replanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY  # noqa: F401  (registers every tool)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autodataagent",
        description="Autonomous data agent: decompose a goal, execute tools, "
        "verify results, recover from failures, produce a report.",
    )
    parser.add_argument("goal", help="the analytical goal in natural language")
    parser.add_argument(
        "--mode", choices=["mock", "llm"], default=None,
        help="planner flavour (default: llm if OPENAI_API_KEY is set, else mock)",
    )
    parser.add_argument(
        "--data-root", default="data",
        help="directory of data sources (default: ./data)",
    )
    parser.add_argument(
        "--outputs-root", default="outputs",
        help="directory for run outputs (default: ./outputs)",
    )
    parser.add_argument(
        "--inject-failure", metavar="STEP[:MODE]", default=None,
        help="failure injection for demos: 's3' (transient) or "
        "'s3:permanent' (survives retries, replan rescues)",
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0,
        help="sandbox timeout in seconds (default: 120)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # pipes / CI logs: emit UTF-8 so Chinese goals survive any codepage
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    config = load_config(
        mode=args.mode,
        data_root=args.data_root,
        outputs_root=args.outputs_root,
        inject_failure=args.inject_failure,
        exec_timeout_s=args.timeout,
    )

    planner = MockPlanner() if config.mode == "mock" else LLMPlanner()

    orchestrator = Orchestrator(
        planner=planner,
        replanner=Replanner(),
        registry=REGISTRY,
        state_factory=StateManager.new_run,
        config=config,
    )

    try:
        summary = orchestrator.run(args.goal)
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    _print_summary(summary)
    return 0 if summary.n_failed == 0 else 1


def _print_summary(summary: RunSummary) -> None:
    w = 64
    print("=" * w)
    print(f"AutoDataAgent run finished: {summary.status}")
    print(f"  goal     : {summary.goal}")
    print(f"  planner  : {summary.planner}   run: {summary.run_id}")
    print(
        f"  steps    : {summary.n_steps} "
        f"(ok {summary.n_succeeded}, recovered {summary.n_recovered}, "
        f"failed {summary.n_failed}, skipped {summary.n_skipped})"
    )
    print(
        f"  recovery : {summary.n_retries} retry(ies), "
        f"{summary.n_replans} replan(s), {summary.duration_ms} ms total"
    )
    print(f"  run dir  : {summary.run_dir}")
    if summary.artifacts:
        print("  artifacts:")
        for a in summary.artifacts:
            mark = "ok" if a.get("valid", True) else "INVALID"
            print(f"    [{mark:>7}] {a['artifact_id']} ({a['kind']}) {a['path']}")
    if summary.report_path:
        print(f"  report   : {summary.report_path}")
    if summary.trace_path:
        print(f"  trace    : {summary.trace_path}")
    print("=" * w)


if __name__ == "__main__":
    sys.exit(main())
