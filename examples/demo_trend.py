"""Scenario 1 (headline demo): monthly trend analysis.

Run from anywhere (repo root recommended)::

    python examples/demo_trend.py

Shows three runs back-to-back:
  1. the clean run (5 steps, all first-attempt)
  2. a transient failure at s3 -> RETRY rescues it
  3. a permanent failure at s3 -> RETRY exhausted -> REPLAN swaps
     aggregate -> aggregate_pivot and finishes the job
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # allow running from any cwd
    sys.path.insert(0, str(ROOT))

from agent.cli import main  # noqa: E402

GOAL = "分析 data/sales.csv 的月度销售趋势，输出月度汇总表和趋势图"

if __name__ == "__main__":
    import os

    os.chdir(ROOT)  # CLI defaults (data/, outputs/) are relative to the repo root

    print("=" * 60)
    print("# 1/3 clean run")
    print("=" * 60)
    main([GOAL])

    print()
    print("=" * 60)
    print("# 2/3 transient failure injected at s3 (recovered by RETRY)")
    print("=" * 60)
    main([GOAL, "--inject-failure", "s3"])

    print()
    print("=" * 60)
    print("# 3/3 permanent failure injected at s3 (REPLAN swaps the tool)")
    print("=" * 60)
    main([GOAL, "--inject-failure", "s3:permanent"])
