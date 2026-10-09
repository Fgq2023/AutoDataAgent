"""SQLite persistence for one agent run (subtopic B).

Every run gets its own ``state.db`` inside the run directory, holding:

  * ``runs``       — run metadata (goal, timestamps)
  * ``steps``      — per-step status / attempts / errors / durations
  * ``artifacts``  — intermediate products, with an ``valid`` flag so that
                     rolled-back artifacts remain queryable for auditing
  * ``provenance`` — artifact lineage: producer step/tool + input artifacts
  * ``trace``      — append-only event log, exported to ``trace.json``
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.core.types import Artifact, ProvenanceRecord, Step, StepStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    goal       TEXT NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    step_id     TEXT PRIMARY KEY,
    description TEXT,
    tool        TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    valid       INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_name ON artifacts(name);
CREATE TABLE IF NOT EXISTS provenance (
    artifact_id TEXT PRIMARY KEY,
    step_id     TEXT NOT NULL,
    tool        TEXT NOT NULL,
    input_ids   TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trace (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    event   TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunStore:
    """Thin SQLite wrapper — one instance per run."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # -- run ----------------------------------------------------------------
    def insert_run(self, run_id: str, goal: str) -> None:
        self.conn.execute(
            "INSERT INTO runs(run_id, goal, started_at) VALUES (?,?,?)",
            (run_id, goal, utcnow()),
        )
        self.conn.commit()

    # -- steps ---------------------------------------------------------------
    def insert_steps(self, steps: list[Step]) -> None:
        self.conn.executemany(
            "INSERT INTO steps(step_id, description, tool) VALUES (?,?,?)",
            [(s.step_id, s.description, s.tool) for s in steps],
        )
        self.conn.commit()

    def update_step(
        self,
        step_id: str,
        status: StepStatus,
        attempts: int | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Update a step row.  ``None`` args keep their current stored value."""
        cur_attempts, cur_error, cur_dur = self.conn.execute(
            "SELECT attempts, error, duration_ms FROM steps WHERE step_id=?",
            (step_id,),
        ).fetchone()
        self.conn.execute(
            "UPDATE steps SET status=?, attempts=?, error=?, duration_ms=?, updated_at=? "
            "WHERE step_id=?",
            (
                status.value if isinstance(status, StepStatus) else str(status),
                cur_attempts if attempts is None else attempts,
                error if error is not None else cur_error,
                cur_dur if duration_ms is None else duration_ms,
                utcnow(),
                step_id,
            ),
        )
        self.conn.commit()

    def step_rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT step_id, description, tool, status, attempts, error, duration_ms "
            "FROM steps ORDER BY rowid"
        )]

    # -- artifacts -------------------------------------------------------------
    def insert_artifact(self, art: Artifact) -> None:
        self.conn.execute(
            "INSERT INTO artifacts(artifact_id, name, kind, path, meta, valid, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                art.artifact_id,
                art.name,
                art.kind,
                art.path,
                json.dumps(art.meta, ensure_ascii=False),
                int(art.valid),
                art.created_at or utcnow(),
            ),
        )
        self.conn.commit()

    def invalidate_artifact(self, artifact_id: str) -> None:
        self.conn.execute(
            "UPDATE artifacts SET valid=0 WHERE artifact_id=?", (artifact_id,)
        )
        self.conn.commit()

    def artifact(self, artifact_id: str) -> Artifact | None:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        return self._row_to_artifact(row) if row else None

    def artifacts_named(self, name: str, valid_only: bool = True) -> list[Artifact]:
        sql = "SELECT * FROM artifacts WHERE name=?"
        if valid_only:
            sql += " AND valid=1"
        sql += " ORDER BY rowid"
        return [self._row_to_artifact(r) for r in self.conn.execute(sql, (name,))]

    def all_artifacts(self, valid_only: bool = True) -> list[Artifact]:
        sql = "SELECT * FROM artifacts"
        if valid_only:
            sql += " WHERE valid=1"
        sql += " ORDER BY rowid"
        return [self._row_to_artifact(r) for r in self.conn.execute(sql)]

    def artifacts_of_step(self, step_id: str, valid_only: bool = False) -> list[Artifact]:
        sql = (
            "SELECT a.* FROM artifacts a JOIN provenance p ON a.artifact_id=p.artifact_id "
            "WHERE p.step_id=?"
        )
        if valid_only:
            sql += " AND a.valid=1"
        sql += " ORDER BY a.rowid"
        return [self._row_to_artifact(r) for r in self.conn.execute(sql, (step_id,))]

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row) -> Artifact:
        return Artifact(
            artifact_id=row["artifact_id"],
            name=row["name"],
            kind=row["kind"],
            path=row["path"],
            meta=json.loads(row["meta"]),
            valid=bool(row["valid"]),
            created_at=row["created_at"],
        )

    # -- provenance --------------------------------------------------------------
    def insert_provenance(self, rec: ProvenanceRecord) -> None:
        self.conn.execute(
            "INSERT INTO provenance(artifact_id, step_id, tool, input_ids, created_at) "
            "VALUES (?,?,?,?,?)",
            (
                rec.artifact_id,
                rec.step_id,
                rec.tool,
                json.dumps(rec.input_artifact_ids),
                rec.created_at or utcnow(),
            ),
        )
        self.conn.commit()

    def provenance(self, artifact_id: str) -> ProvenanceRecord | None:
        row = self.conn.execute(
            "SELECT * FROM provenance WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if not row:
            return None
        return ProvenanceRecord(
            artifact_id=row["artifact_id"],
            step_id=row["step_id"],
            tool=row["tool"],
            input_artifact_ids=json.loads(row["input_ids"]),
            created_at=row["created_at"],
        )

    def all_provenance(self) -> list[ProvenanceRecord]:
        return [
            ProvenanceRecord(
                artifact_id=r["artifact_id"],
                step_id=r["step_id"],
                tool=r["tool"],
                input_artifact_ids=json.loads(r["input_ids"]),
                created_at=r["created_at"],
            )
            for r in self.conn.execute("SELECT * FROM provenance ORDER BY rowid")
        ]

    # -- trace --------------------------------------------------------------------
    def append_trace(self, event: str, payload: dict[str, Any] | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO trace(ts, event, payload) VALUES (?,?,?)",
            (utcnow(), event, json.dumps(payload or {}, ensure_ascii=False, default=str)),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def all_trace(self) -> list[dict[str, Any]]:
        return [
            {"seq": r["seq"], "ts": r["ts"], "event": r["event"], "payload": json.loads(r["payload"])}
            for r in self.conn.execute("SELECT * FROM trace ORDER BY seq")
        ]

    def close(self) -> None:
        self.conn.close()
