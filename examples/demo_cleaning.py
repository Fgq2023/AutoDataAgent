"""Scenario 2: data cleaning with a before/after report.

Run from anywhere (repo root recommended)::

    python examples/demo_cleaning.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # allow running from any cwd
    sys.path.insert(0, str(ROOT))

from agent.cli import main  # noqa: E402

if __name__ == "__main__":
    import os

    os.chdir(ROOT)  # CLI defaults (data/, outputs/) are relative to the repo root
    main(["清洗 data/sales.csv 中的缺失值和重复行"])
