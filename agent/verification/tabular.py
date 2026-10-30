"""Declarative aggregate contracts over CSV or a single SQLite table.

Reference evaluation uses stdlib records/Decimal, independent of pandas tools
and generated SQL. No executable expressions or model-provided reference query.
"""
import csv
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from agent.core.types import CheckResult


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite numeric value")
    return result


def validate_tabular(c):
    allowed = {"kind", "artifact", "source", "source_type", "table", "group_by", "metrics", "filters", "tolerance"}
    if set(c) - allowed:
        raise ValueError("unknown tabular contract fields")
    if c.get("source_type") not in ("csv", "sqlite"):
        raise ValueError("source_type must be csv or sqlite")
    if c["source_type"] == "sqlite" and (not isinstance(c.get("table"), str) or not c["table"]):
        raise ValueError("SQLite contract requires a table")
    groups, metrics, filters = c.get("group_by"), c.get("metrics"), c.get("filters", [])
    if not isinstance(groups, list) or not isinstance(metrics, list) or not metrics or not isinstance(filters, list):
        raise ValueError("group_by/metrics/filters must be lists, metrics must not be empty")
    outputs = []
    for g in groups:
        if not isinstance(g, dict) or set(g) - {"column", "output", "transform"}:
            raise ValueError("invalid group specification")
        if g.get("transform", "identity") not in ("identity", "month", "year"):
            raise ValueError("unsupported grouping transform")
        if not all(isinstance(g.get(k), str) and g[k] for k in ("column", "output")):
            raise ValueError("group requires column/output")
        outputs.append(g["output"])
    for m in metrics:
        if not isinstance(m, dict) or set(m) - {"op", "column", "output"} or m.get("op") not in ("sum", "count", "mean"):
            raise ValueError("unsupported metric")
        if not isinstance(m.get("output"), str) or not m["output"]:
            raise ValueError("metric requires output")
        if m["op"] != "count" and (not isinstance(m.get("column"), str) or not m["column"]):
            raise ValueError("numeric metric requires column")
        if m["op"] == "count" and "column" in m:
            raise ValueError("count counts rows; do not specify column")
        outputs.append(m["output"])
    if len(outputs) != len(set(outputs)):
        raise ValueError("duplicate contract output columns")
    for f in filters:
        if not isinstance(f, dict) or set(f) != {"column", "op", "value", "type"}:
            raise ValueError("filter requires column/op/value/type")
        if not isinstance(f["column"], str) or not f["column"]:
            raise ValueError("filter requires column")
        if f["op"] not in ("eq", "ne", "lt", "lte", "gt", "gte") or f["type"] not in ("text", "number"):
            raise ValueError("unsupported filter")
        if f["type"] == "number":
            number(f["value"])
        elif not isinstance(f["value"], str):
            raise ValueError("text filter value must be a string")
    if number(c.get("tolerance", "0.005")) < 0:
        raise ValueError("negative tolerance")


def read_source(c):
    path = Path(c["source"]).resolve()
    if c["source_type"] == "csv":
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            columns, rows = reader.fieldnames or [], list(reader)
    else:
        table = c["table"].replace('"', '""')
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            cursor = db.execute(f'SELECT * FROM "{table}"')
            columns = [d[0] for d in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor]
    required = {g["column"] for g in c["group_by"]}
    if len(columns) != len(set(columns)):
        raise ValueError("duplicate source columns")
    required.update(m["column"] for m in c["metrics"] if m["op"] != "count")
    required.update(f["column"] for f in c.get("filters", []))
    if required - set(columns):
        raise ValueError(f"source lacks required columns: {sorted(required - set(columns))}")
    return rows


def evaluate(c):
    groups = {} if c["group_by"] else {(): [0, [Decimal(0) for _ in c["metrics"]]]}
    for row in read_source(c):
        keep = True
        for f in c.get("filters", []):
            convert = number if f["type"] == "number" else str
            a, b = convert(row[f["column"]]), convert(f["value"])
            matched = {"eq": a == b, "ne": a != b, "lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[f["op"]]
            if not matched:
                keep = False
                break
        if not keep:
            continue
        key = []
        for g in c["group_by"]:
            value = row[g["column"]]
            if value is None or value == "":
                raise ValueError("missing grouping value")
            transform = g.get("transform", "identity")
            if transform != "identity":
                value = datetime.fromisoformat(str(value)).strftime("%Y-%m" if transform == "month" else "%Y")
            key.append(str(value))
        bucket = groups.setdefault(tuple(key), [0, [Decimal(0) for _ in c["metrics"]]])
        bucket[0] += 1
        for i, m in enumerate(c["metrics"]):
            bucket[1][i] += 1 if m["op"] == "count" else number(row[m["column"]])
    return {key: {m["output"]: totals[i] / count if m["op"] == "mean" else totals[i]
                  for i, m in enumerate(c["metrics"])} for key, (count, totals) in groups.items()}


def check_tabular(c, artifact, run_dir):
    name = f"task:tabular_aggregate:{artifact.name}"
    requirement = {k: c[k] for k in ("group_by", "metrics", "filters") if k in c}
    prefix = "Required " + json.dumps(requirement, ensure_ascii=False) + ": "
    try:
        expected = evaluate(c)
        keys = [g["output"] for g in c["group_by"]]
        fields = keys + [m["output"] for m in c["metrics"]]
        actual = {}
        with (Path(run_dir) / artifact.path).open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if len(reader.fieldnames or []) != len(fields) or set(reader.fieldnames or []) != set(fields):
                raise ValueError(f"expected output columns {fields}")
            for row in reader:
                key = tuple(row[k] for k in keys)
                if key in actual:
                    raise ValueError("duplicate output group")
                actual[key] = row
        if set(actual) != set(expected):
            raise ValueError(f"group set mismatch: expected {len(expected)}, actual {len(actual)}")
        tolerance = number(c.get("tolerance", "0.005"))
        for key, values in expected.items():
            for metric, target in values.items():
                op = next(m["op"] for m in c["metrics"] if m["output"] == metric)
                allowed = Decimal(0) if op == "count" else tolerance
                if abs(number(actual[key][metric]) - target) > allowed:
                    raise ValueError(f"group={key}, {metric}: expected {target}, got {actual[key][metric]}")
        return CheckResult(name, True, prefix + "independent record evaluation passed")
    except Exception as exc:
        return CheckResult(name, False, prefix + str(exc))
