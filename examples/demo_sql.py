"""Scenario 3: SQL question answering over the orders database.

Run from anywhere (repo root recommended)::

    python examples/demo_sql.py
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
    main(["查询 data/orders.db 中 2024 年每个客户的总消费金额"])
