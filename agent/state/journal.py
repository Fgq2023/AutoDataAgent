"""Versioned run manifests stored transactionally in each run's SQLite database."""
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(state, payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    plan = json.dumps(payload["plan"], sort_keys=True)
    plan_hash = hashlib.sha256(plan.encode()).hexdigest()
    with state.store.conn:
        state.store.conn.execute(
            "INSERT OR IGNORE INTO plan_revisions(digest,plan) VALUES (?,?)", (plan_hash, plan)
        )
        state.store.conn.execute(
            "INSERT INTO resume_manifest VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, digest=excluded.digest",
            (encoded, hashlib.sha256(encoded.encode()).hexdigest()),
        )


def initialize(state, payload):
    state.store.conn.execute("CREATE TABLE IF NOT EXISTS resume_manifest (id INTEGER PRIMARY KEY, payload TEXT, digest TEXT)")
    state.store.conn.execute("CREATE TABLE IF NOT EXISTS plan_revisions (version INTEGER PRIMARY KEY AUTOINCREMENT, digest TEXT UNIQUE, plan TEXT)")
    save(state, payload)


def load(store):
    if not store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='resume_manifest'").fetchone():
        raise ValueError("legacy run has no resume manifest; view/export only")
    row = store.conn.execute("SELECT payload,digest FROM resume_manifest WHERE id=1").fetchone()
    if not row or hashlib.sha256(row["payload"].encode()).hexdigest() != row["digest"]:
        raise ValueError("resume manifest integrity check failed")
    return json.loads(row["payload"])
