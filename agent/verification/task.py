"""Explicit task contracts, independent of model-generated plans.

Supports generic tabular aggregates and the legacy orders/customers contract.
Requirements are explicit; no implicit natural-language inference.
"""
import csv
import json
import sqlite3
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from agent.core.types import CheckResult


def validate_contracts(contracts):
    if not isinstance(contracts, list):
        raise ValueError("task contracts must be a list")
    seen = set()
    for c in contracts:
        if not isinstance(c, dict):
            raise ValueError("task contract must be an object")
        if c.get("kind") not in ("customer_totals_year", "tabular_aggregate"):
            raise ValueError("unsupported task contract kind")
        if c["kind"] == "tabular_aggregate":
            from agent.verification.tabular import validate_tabular
            validate_tabular(c)
        elif not isinstance(c.get("year"), int) or not 1900 <= c["year"] <= 2100:
            raise ValueError("contract requires a valid integer year")
        for key in ("artifact", "source"):
            if not isinstance(c.get(key), str) or not c[key]:
                raise ValueError(f"contract requires {key}")
        if c["artifact"] in seen:
            raise ValueError("duplicate task contract artifact")
        seen.add(c["artifact"])


def load_contracts(path):
    p = Path(path).resolve()
    contracts = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(contracts, list):
        raise ValueError("task contract file must contain a JSON list")
    validate_contracts(contracts)
    for c in contracts:
        c["source"] = str((p.parent / c["source"]).resolve())
    return contracts


def check_contract(contract, artifact, run_dir):
    if contract["kind"] == "tabular_aggregate":
        from agent.verification.tabular import check_tabular
        return check_tabular(contract, artifact, run_dir)
    year = str(contract["year"])
    label = f"task:customer_totals_year:{artifact.name}"
    prefix = f"Required: per-customer COUNT orders and SUM amount for year={year}. "
    try:
        source = Path(contract["source"]).resolve()
        if not source.is_file():
            raise ValueError("contract source does not exist")
        # Read individual records, not the agent's aggregate query/results.
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
            names = dict(db.execute("SELECT id, name FROM customers"))
            expected = defaultdict(lambda: [0, Decimal(0)])
            for customer, date, amount in db.execute("SELECT customer_id, order_date, amount FROM orders"):
                if str(date)[:4] == year:
                    if customer not in names:
                        raise ValueError("order references missing customer")
                    count = expected[names[customer]]
                    count[0] += 1
                    count[1] += Decimal(str(amount))
        with (Path(run_dir) / artifact.path).open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if set(reader.fieldnames or []) != {"customer", "n_orders", "total_spent"}:
                raise ValueError("result columns must be customer,n_orders,total_spent")
            rows = list(reader)
        if len(rows) != len(expected) or {r["customer"] for r in rows} != set(expected):
            raise ValueError("customer set or row count differs from required year")
        for row in rows:
            count, total = expected[row["customer"]]
            actual = Decimal(row["total_spent"])
            if not actual.is_finite() or Decimal(row["n_orders"]) != count or abs(actual - total) > Decimal("0.005"):
                raise ValueError(f"customer={row['customer']}: expected n_orders={count}, total_spent={total}; got n_orders={row['n_orders']}, total_spent={row['total_spent']}")
        return CheckResult(label, True, prefix + "independent source-record comparison passed")
    except Exception as exc:
        return CheckResult(label, False, prefix + str(exc))
