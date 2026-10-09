"""Generate the deterministic demo datasets under ``data/``.

  * ``data/sales.csv``  — 24 months x 3 regions x 2 products, with injected
    quality issues (missing revenues, duplicated rows, negative prices)
  * ``data/orders.db``  — SQLite: customers + orders (2024-2025)

Everything is seeded, so regeneration is byte-stable (modulo timestamps in
the DB), which keeps demos, tests and CI reproducible.
"""

from __future__ import annotations

import csv
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def make_sales_csv() -> Path:
    random.seed(42)
    path = DATA_DIR / "sales.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    regions = ["华东", "华北", "华南"]
    products = [("A100", 39.9), ("B200", 129.0)]
    rows: list[list] = []
    for i in range(24):  # 2024-01 .. 2025-12, real calendar months
        month = date(2024 + i // 12, i % 12 + 1, 1).strftime("%Y-%m")
        for r_idx, region in enumerate(regions):
            for p_idx, (product, price) in enumerate(products):
                base = 120 + 60 * r_idx + 80 * p_idx
                seasonal = 1.0 + 0.35 * ((i % 12) / 11.0)  # ramp within year
                noise = random.uniform(0.75, 1.25)
                units = max(1, round(base * seasonal * noise))
                rows.append([f"{month}-15", region, product, units, price,
                             round(units * price, 2)])

    # ---- injected quality issues -------------------------------------- #
    # 5 rows with missing revenue
    for k in range(5):
        rows[k * 29][5] = ""
    # 4 exact duplicates
    for k in range(4):
        rows.append(list(rows[30 + k * 7]))
    # 3 rows with negative price (and hence absurd revenue)
    for k in range(3):
        row = rows[60 + k * 11]
        row[4] = -abs(float(row[4]))
        row[5] = round(float(row[3]) * float(row[4]), 2)

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "region", "product", "units", "price", "revenue"])
        writer.writerows(rows)
    return path


def make_orders_db() -> Path:
    random.seed(7)
    path = DATA_DIR / "orders.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE customers (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            city TEXT NOT NULL
        );
        CREATE TABLE orders (
            id          INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            order_date  TEXT NOT NULL,
            amount      REAL NOT NULL
        );
        """
    )

    names = [
        ("张伟", "深圳"), ("王芳", "广州"), ("李娜", "杭州"),
        ("刘强", "上海"), ("陈静", "北京"), ("杨洋", "成都"),
    ]
    cur.executemany("INSERT INTO customers(name, city) VALUES (?, ?)", names)

    orders = []
    oid = 0
    for year in (2024, 2025):
        d = date(year, 1, 1)
        while d <= date(year, 12, 31):
            for k in range(random.randint(0, 2)):
                oid += 1
                cust = random.randint(1, len(names))
                amount = round(random.uniform(50, 2500), 2)
                orders.append((oid, cust, d.isoformat(), amount))
            d += timedelta(days=1)
    cur.executemany(
        "INSERT INTO orders(id, customer_id, order_date, amount) VALUES (?, ?, ?, ?)",
        orders,
    )
    conn.commit()
    conn.close()
    return path


if __name__ == "__main__":
    sales = make_sales_csv()
    orders = make_orders_db()
    print(f"wrote {sales}")
    print(f"wrote {orders}")
