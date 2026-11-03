"""Prepare independent_v1 from three attributed public UCI datasets.

Download/normalization and reference validation only; no model calls.
Agent-facing tasks and grader-only contracts/answers are stored separately.
"""
import csv
import hashlib
import io
import json
import sqlite3
import sys
import urllib.request
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.verification.task import validate_contracts
from agent.verification.tabular import evaluate

SOURCES = {
    "bike": {"id": 275, "slug": "bike%2Bsharing%2Bdataset", "doi": "10.24432/C5W894", "author": "Hadi Fanaee-T (2013)", "split": "dev"},
    "wine": {"id": 186, "slug": "wine%2Bquality", "doi": "10.24432/C56S3T", "author": "Cortez et al. (2009)", "split": "validation"},
    "energy": {"id": 242, "slug": "energy%2Befficiency", "doi": "10.24432/C51307", "author": "Tsanas and Xifara (2012)", "split": "test"},
}


def f(column, op, value, type="number"):
    return {"column": column, "op": op, "value": value, "type": type}


def g(column, transform="identity", output=None):
    return {"column": column, "transform": transform, "output": output or column}


def m(op, column=None, output=None):
    return {"op": op, "output": output or ("n_rows" if op == "count" else op + "_" + column), **({"column": column} if column else {})}


def definitions():
    # Authored task specifications, not model-generated SQL or fault injections.
    return [
        ("bike", "bike_day", "2011年按月份汇总总租车量cnt，月份列命名month。", [g("dteday", "month", "month")], [m("sum", "cnt")], [f("yr", "eq", 0)]),
        ("bike", "bike_day", "2012年按月份计算cnt的日均值和天数。月份列命名month。", [g("dteday", "month", "month")], [m("mean", "cnt"), m("count")], [f("yr", "eq", 1)]),
        ("bike", "bike_day", "按season汇总casual和registered租车量。", [g("season")], [m("sum", "casual"), m("sum", "registered")], []),
        ("bike", "bike_day", "2012年按workingday计算日均cnt。", [g("workingday")], [m("mean", "cnt")], [f("yr", "eq", 1)]),
        ("bike", "bike_day", "湿度hum不小于0.8时按weathersit计算日均cnt与天数。", [g("weathersit")], [m("mean", "cnt"), m("count")], [f("hum", "gte", 0.8)]),
        ("bike", "bike_day", "温度temp不小于0.5且workingday为1时，按season汇总cnt。", [g("season")], [m("sum", "cnt")], [f("temp", "gte", 0.5), f("workingday", "eq", 1)]),
        ("bike", "bike_hour", "2011年工作日按hr汇总registered。", [g("hr")], [m("sum", "registered")], [f("yr", "eq", 0), f("workingday", "eq", 1)]),
        ("bike", "bike_hour", "2012年非工作日按hr计算平均casual。", [g("hr")], [m("mean", "casual")], [f("yr", "eq", 1), f("workingday", "eq", 0)]),
        ("bike", "bike_hour", "按weathersit和workingday联合分组计算平均cnt。", [g("weathersit"), g("workingday")], [m("mean", "cnt")], []),
        ("bike", "bike_hour", "只保留hr大于等于7且小于10的记录，按weekday汇总cnt。", [g("weekday")], [m("sum", "cnt")], [f("hr", "gte", 7), f("hr", "lt", 10)]),
        ("bike", "bike_day", "将2011-07-01到2011-10-01（不含结束日）的记录按month分组，计算cnt总量。month从dteday提取。", [g("dteday", "month", "month")], [m("sum", "cnt")], [f("dteday", "gte", "2011-07-01", "text"), f("dteday", "lt", "2011-10-01", "text")]),
        ("bike", "bike_day", "按year汇总cnt和记录数，year从dteday提取。", [g("dteday", "year", "year")], [m("sum", "cnt"), m("count")], []),
        ("wine", "wine_red", "按quality分组计算alcohol与sulphates均值及样本数。", [g("quality")], [m("mean", "alcohol"), m("mean", "sulphates"), m("count")], []),
        ("wine", "wine_red", "quality不小于7时计算alcohol总体均值与样本数。", [], [m("mean", "alcohol"), m("count")], [f("quality", "gte", 7)]),
        ("wine", "wine_red", "alcohol不小于10且volatile_acidity小于0.5时按quality计数。", [g("quality")], [m("count")], [f("alcohol", "gte", 10), f("volatile_acidity", "lt", 0.5)]),
        ("wine", "wine_red", "quality小于6时按quality计算residual_sugar与chlorides均值。", [g("quality")], [m("mean", "residual_sugar"), m("mean", "chlorides")], [f("quality", "lt", 6)]),
        ("wine", "wine_white", "按quality计算alcohol均值和样本数。", [g("quality")], [m("mean", "alcohol"), m("count")], []),
        ("wine", "wine_white", "quality不小于7且alcohol不小于11时计算density总体均值。", [], [m("mean", "density")], [f("quality", "gte", 7), f("alcohol", "gte", 11)]),
        ("wine", "wine_white", "residual_sugar不小于10时按quality计算citric_acid均值与样本数。", [g("quality")], [m("mean", "citric_acid"), m("count")], [f("residual_sugar", "gte", 10)]),
        ("wine", "wine_white", "pH不小于3且pH小于3.3时按quality计算free_sulfur_dioxide总量与均值。", [g("quality")], [m("sum", "free_sulfur_dioxide"), m("mean", "free_sulfur_dioxide")], [f("pH", "gte", 3), f("pH", "lt", 3.3)]),
        ("energy", "energy", "按orientation计算heating_load与cooling_load均值。", [g("orientation")], [m("mean", "heating_load"), m("mean", "cooling_load")], []),
        ("energy", "energy", "按overall_height统计样本数与平均heating_load。", [g("overall_height")], [m("count"), m("mean", "heating_load")], []),
        ("energy", "energy", "glazing_area不小于0.25时按orientation计算平均cooling_load。", [g("orientation")], [m("mean", "cooling_load")], [f("glazing_area", "gte", 0.25)]),
        ("energy", "energy", "按glazing_area与orientation联合分组计算平均heating_load。", [g("glazing_area"), g("orientation")], [m("mean", "heating_load")], []),
        ("energy", "energy", "relative_compactness不小于0.8时计算heating_load总体均值和样本数。", [], [m("mean", "heating_load"), m("count")], [f("relative_compactness", "gte", 0.8)]),
        ("energy", "energy", "overall_height大于3.5时按glazing_area_distribution计算平均cooling_load。", [g("glazing_area_distribution")], [m("mean", "cooling_load")], [f("overall_height", "gt", 3.5)]),
        ("energy", "energy", "按orientation计算heating_load与cooling_load总和及样本数。", [g("orientation")], [m("sum", "heating_load"), m("sum", "cooling_load"), m("count")], []),
        ("energy", "energy", "wall_area不小于300且roof_area小于200时，按orientation计算平均heating_load。", [g("orientation")], [m("mean", "heating_load")], [f("wall_area", "gte", 300), f("roof_area", "lt", 200)]),
        ("energy", "energy", "glazing_area等于0时按orientation计数并计算平均cooling_load。", [g("orientation")], [m("count"), m("mean", "cooling_load")], [f("glazing_area", "eq", 0)]),
        ("energy", "energy", "heating_load小于15时按overall_height计算平均cooling_load和样本数。", [g("overall_height")], [m("mean", "cooling_load"), m("count")], [f("heating_load", "lt", 15)]),
    ]


def canonical(v):
    if v is None:
        raise ValueError("unexpected missing data")
    try:
        d = Decimal(str(v))
        if not d.is_finite():
            raise ValueError("nonfinite value")
        return format(d.normalize(), "f")
    except ArithmeticError:
        return str(v)


def sql_reference(c, dbpath, table):
    quote = lambda s: '"' + s.replace('"', '""') + '"'
    groups = []
    for group in c["group_by"]:
        expr = quote(group["column"])
        if group.get("transform") in ("month", "year"):
            expr = f"substr({expr},1,{7 if group['transform'] == 'month' else 4})"
        groups.append(expr)
    metrics = ["COUNT(*)" if m["op"] == "count" else ("AVG" if m["op"] == "mean" else "SUM") + "(" + quote(m["column"]) + ")" for m in c["metrics"]]
    operators = {"eq": "=", "ne": "!=", "gte": ">=", "gt": ">", "lt": "<", "lte": "<="}
    where = [quote(f["column"]) + operators[f["op"]] + "?" for f in c["filters"]]
    query = "SELECT " + ",".join(groups + metrics) + " FROM " + quote(table)
    if where:
        query += " WHERE " + " AND ".join(where)
    if groups:
        query += " GROUP BY " + ",".join(groups)
    with sqlite3.connect(dbpath) as db:
        result = db.execute(query, [f["value"] for f in c["filters"]]).fetchall()
    return {tuple(str(v) for v in row[:len(groups)]): {m["output"]: Decimal(str(row[len(groups) + i])) for i, m in enumerate(c["metrics"])} for row in result}


def main():
    base = ROOT / "outputs/independent_v1"
    raw = base / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    provenance, tables = {}, {}
    for domain, meta in SOURCES.items():
        path = raw / (domain + ".zip")
        url = f"https://archive.ics.uci.edu/static/public/{meta['id']}/{meta['slug']}.zip"
        if not path.exists():
            request = urllib.request.Request(url, headers={"User-Agent": "AutoDataAgent/0.1 dataset research"})
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read(10_000_001)
            if len(payload) > 10_000_000:
                raise ValueError("unexpected archive size")
            zipfile.ZipFile(io.BytesIO(payload)).testzip()
            path.write_bytes(payload)
        lock = ROOT / "benchmarks/independent_v1/sources.lock.json"
        if lock.exists():
            expected_hash = json.loads(lock.read_text(encoding="utf-8"))[domain]["sha256"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"upstream archive changed: {domain}; review before updating the lock")
        provenance[domain] = {**meta, "url": url, "license": "CC-BY-4.0", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        with zipfile.ZipFile(path) as archive:
            if domain in ("bike", "wine"):
                members = [("day.csv", "bike_day"), ("hour.csv", "bike_hour")] if domain == "bike" else [("winequality-red.csv", "wine_red"), ("winequality-white.csv", "wine_white")]
                for member, name in members:
                    text = archive.read(member).decode("utf-8-sig")
                    reader = csv.reader(io.StringIO(text), delimiter=";" if domain == "wine" else ",")
                    header = [h.replace(" ", "_") for h in next(reader)]
                    tables[name] = (domain, header, [[canonical(v) for v in row] for row in reader])
            else:
                import openpyxl
                member = next(n for n in archive.namelist() if n.endswith(".xlsx"))
                book = openpyxl.load_workbook(io.BytesIO(archive.read(member)), read_only=True, data_only=True)
                values = list(book.active.values)
                header = ["relative_compactness", "surface_area", "wall_area", "roof_area", "overall_height", "orientation", "glazing_area", "glazing_area_distribution", "heating_load", "cooling_load"]
                # Original workbook includes empty trailing rows/columns.
                data = [[canonical(v) for v in row[:10]] for row in values[1:] if any(v is not None for v in row[:10])]
                book.close()
                tables["energy"] = (domain, header, data)
    for name, (domain, header, data) in tables.items():
        folder = base / "inputs" / domain
        folder.mkdir(parents=True, exist_ok=True)
        csvpath = folder / (name + ".csv")
        with csvpath.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(data)
        dbpath = folder / "data.db"
        quote = lambda s: '"' + s.replace('"', '""') + '"'
        with sqlite3.connect(dbpath) as db:
            db.execute(f"DROP TABLE IF EXISTS {quote(name)}")
            numeric = [all(_is_number(row[i]) for row in data) for i in range(len(header))]
            columns = ",".join(quote(h) + (" NUMERIC" if numeric[i] else " TEXT") for i, h in enumerate(header))
            db.execute(f"CREATE TABLE {quote(name)} ({columns})")
            db.executemany(f"INSERT INTO {quote(name)} VALUES ({','.join('?' for _ in header)})", data)
        provenance.setdefault("prepared", {})[name] = {"rows": len(data), "columns": header, "csv_sha256": hashlib.sha256(csvpath.read_bytes()).hexdigest()}
    provenance["sqlite_sha256"] = {domain: hashlib.sha256((base / "inputs" / domain / "data.db").read_bytes()).hexdigest() for domain in SOURCES}
    tasks, checks = [], []
    grader = base / "grader_only"
    grader.mkdir(exist_ok=True)
    for i, (domain, table, question, groups, metrics, filters) in enumerate(definitions(), 1):
        task_id = f"I{i:03d}"
        artifact = "answer_" + task_id
        source_type = "csv" if i % 2 else "sqlite"
        filename = table + ".csv" if source_type == "csv" else "data.db"
        c = {"kind": "tabular_aggregate", "artifact": artifact, "source": str(base / "inputs" / domain / filename), "source_type": source_type,
             "group_by": groups, "metrics": metrics, "filters": filters, "tolerance": "0.000001"}
        if source_type == "sqlite":
            c["table"] = table
        validate_contracts([c])
        answer = evaluate(c)
        # Cross-check both representations before any model evaluation.
        other = dict(c, source=str(base / "inputs" / domain / ("data.db" if source_type == "csv" else table + ".csv")), source_type="sqlite" if source_type == "csv" else "csv", table=table)
        alternate = evaluate(other)
        if answer.keys() != alternate.keys() or any(abs(v - alternate[k][m]) > Decimal("0.000001") for k, row in answer.items() for m, v in row.items()):
            raise ValueError(f"representation mismatch: {task_id}")
        sql_answer = sql_reference(c, base / "inputs" / domain / "data.db", table)
        if answer.keys() != sql_answer.keys() or any(abs(v - sql_answer[k][m]) > Decimal("0.000001") for k, row in answer.items() for m, v in row.items()):
            raise ValueError(f"independent SQL oracle mismatch: {task_id}")
        if not answer:
            raise ValueError(f"unexpected empty answer: {task_id}")
        fields = [g["output"] for g in groups] + [m["output"] for m in metrics]
        with (grader / (task_id + ".csv")).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            for key, values in sorted(answer.items()):
                writer.writerow([*key, *[str(values[m["output"]]) for m in metrics]])
        (grader / (task_id + ".contract.json")).write_text(json.dumps([c], ensure_ascii=False, indent=2), encoding="utf-8")
        prompt = f"使用{filename}" + (f"中的{table}表" if source_type == "sqlite" else "") + "。" + question
        for group in groups:
            if group.get("transform") in ("month", "year"):
                prompt += f" {group['output']}必须使用{'YYYY-MM' if group['transform'] == 'month' else 'YYYY'}字符串格式。"
        prompt += f" 输出列为{','.join(fields)}，表格产物命名{artifact}，并生成final_report。不去重，不删行，筛选仅按题意；不要求排序。"
        tasks.append({"task_id": task_id, "split": SOURCES[domain]["split"], "domain": domain, "table": table, "source_type": source_type, "data_root": "inputs/" + domain, "question": prompt, "artifact": artifact,
                      "group_count": len(groups), "filter_count": len(filters), "metric_ops": [m["op"] for m in metrics]})
        checks.append({"task_id": task_id, "reference_groups": len(answer), "representation_check": "passed", "sql_oracle_check": "passed"})
    (base / "tasks.jsonl").write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks), encoding="utf-8")
    (base / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    (base / "reference_checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    catalog = ROOT / "benchmarks/independent_v1"
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "tasks.jsonl").write_bytes((base / "tasks.jsonl").read_bytes())
    (catalog / "sources.lock.json").write_text(json.dumps({k: provenance[k] for k in SOURCES}, indent=2), encoding="utf-8")
    print(json.dumps({"tasks": len(tasks), "dev": 12, "validation": 8, "test": 10, "reference_checks": len(checks), "output": str(base)}, ensure_ascii=False))


def _is_number(value):
    try:
        return Decimal(value).is_finite()
    except ArithmeticError:
        return False


if __name__ == "__main__":
    main()
