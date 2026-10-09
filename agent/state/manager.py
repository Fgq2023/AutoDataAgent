"""The state manager (subtopic B) — one instance per run.

Owns the run directory and its SQLite store, and is the single source of
truth for:

  * step progress (mark / checkpoint / rollback)
  * artifact registration and lookup (with versioned ids on re-runs)
  * provenance records
  * recovery decisions (policy table from :mod:`agent.state.recovery`)
  * the append-only event trace exported as ``trace.json``
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.core.types import (
    Artifact,
    ProvenanceRecord,
    RecoveryAction,
    Step,
    StepStatus,
    TaskGraph,
    Verdict,
)
from agent.state.provenance import render_lineage
from agent.state.recovery import decide_recovery
from agent.state.store import RunStore, utcnow

if TYPE_CHECKING:  # pragma: no cover
    from agent.tools.base import ArtifactSpec


class StateManager:
    def __init__(self, run_dir: str | Path, store: RunStore, run_id: str,
                 goal: str, graph: TaskGraph):
        self.run_dir = Path(run_dir)
        self.store = store
        self.run_id = run_id
        self.goal = goal
        self.graph = graph

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def new_run(cls, outputs_root: str | Path, goal: str,
                graph: TaskGraph) -> "StateManager":
        """Create the run directory, state DB and initial pending rows."""
        root = Path(outputs_root)
        root.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"run_{stamp}"
        n = 1
        while (root / run_id).exists():  # same-second collisions
            n += 1
            run_id = f"run_{stamp}_{n}"

        run_dir = root / run_id
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

        store = RunStore(run_dir / "state.db")
        store.insert_run(run_id, goal)
        store.insert_steps(graph.steps)
        return cls(run_dir, store, run_id, goal, graph)

    # ------------------------------------------------------------------ #
    # Trace
    # ------------------------------------------------------------------ #
    def trace(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.store.append_trace(event, payload)

    def export_trace(self) -> Path:
        path = self.run_dir / "trace.json"
        path.write_text(
            json.dumps(self.store.all_trace(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------ #
    # Step progress
    # ------------------------------------------------------------------ #
    def mark(self, step_id: str, status: StepStatus, error: str | None = None,
             attempts: int | None = None, duration_ms: int | None = None) -> None:
        self.store.update_step(step_id, status, attempts=attempts,
                               error=error, duration_ms=duration_ms)

    def checkpoint(self, step: Step, result, verdict: Verdict,
                   final_status: StepStatus) -> None:
        """Finalise a successful step attempt: status + verdict into the store."""
        self.store.update_step(
            step.step_id,
            final_status,
            attempts=result.attempts,
            error=None,
            duration_ms=result.duration_ms,
        )
        self.trace(
            "step_succeeded" if final_status == StepStatus.SUCCEEDED else "step_recovered",
            {
                "step_id": step.step_id,
                "tool": step.tool,
                "attempts": result.attempts,
                "outputs": result.outputs,
                "verdict": verdict.to_dict(),
            },
        )

    def rollback(self, to_step_id: str) -> list[str]:
        """Invalidate artifacts produced by steps *after* ``to_step_id`` and
        reset those steps to PENDING so they can be re-executed."""
        ids = self.graph.ids()
        idx = ids.index(to_step_id)
        affected: list[str] = []
        for sid in ids[idx + 1:]:
            for art in self.store.artifacts_of_step(sid):
                self.store.invalidate_artifact(art.artifact_id)
            self.store.update_step(sid, StepStatus.PENDING, error=None, attempts=0)
            self.graph.mark(sid, StepStatus.PENDING)
            affected.append(sid)
        self.trace("rollback", {"to_step": to_step_id, "affected_steps": affected})
        return affected

    # ------------------------------------------------------------------ #
    # Artifacts & provenance
    # ------------------------------------------------------------------ #
    def register_artifacts(self, step: Step, specs: "list[ArtifactSpec]",
                           input_artifact_ids: list[str]) -> list[Artifact]:
        """Persist freshly produced artifacts + provenance records.

        Ids are versioned: a name registered twice gets ``_v2``, ``_v3``...
        so re-executed steps never shadow audit history.
        """
        arts: list[Artifact] = []
        for spec in specs:
            name = spec.name
            n_prior = len(self.store.artifacts_named(name, valid_only=False))
            artifact_id = name if n_prior == 0 else f"{name}_v{n_prior + 1}"
            art = Artifact(
                artifact_id=artifact_id,
                name=name,
                kind=spec.kind,
                path=spec.path,
                meta=spec.meta,
                valid=True,
                created_at=utcnow(),
            )
            self.store.insert_artifact(art)
            self.store.insert_provenance(
                ProvenanceRecord(
                    artifact_id=artifact_id,
                    step_id=step.step_id,
                    tool=step.tool,
                    input_artifact_ids=list(input_artifact_ids),
                    created_at=art.created_at,
                )
            )
            arts.append(art)
        self.trace(
            "artifacts_registered",
            {
                "step_id": step.step_id,
                "tool": step.tool,
                "artifacts": [
                    {"artifact_id": a.artifact_id, "kind": a.kind, "path": a.path}
                    for a in arts
                ],
                "inputs": input_artifact_ids,
            },
        )
        return arts

    def invalidate_step_artifacts(self, step_id: str) -> list[str]:
        invalidated: list[str] = []
        for art in self.store.artifacts_of_step(step_id):
            self.store.invalidate_artifact(art.artifact_id)
            invalidated.append(art.artifact_id)
        if invalidated:
            self.trace("artifact_invalidated", {"step_id": step_id, "artifacts": invalidated})
        return invalidated

    def lookup(self, name: str) -> Artifact:
        """Latest *valid* artifact registered under *name*."""
        found = self.store.artifacts_named(name, valid_only=True)
        if found:
            return found[-1]
        valid_names = sorted({a.name for a in self.store.all_artifacts(valid_only=True)})
        raise KeyError(
            f"no valid artifact named {name!r}; valid names: {valid_names}"
        )

    def artifact(self, artifact_id: str) -> Artifact:
        art = self.store.artifact(artifact_id)
        if art is None:
            raise KeyError(f"unknown artifact id: {artifact_id}")
        return art

    # ------------------------------------------------------------------ #
    # Recovery decisions
    # ------------------------------------------------------------------ #
    def decide_recovery(self, step_id: str, classification: str,
                        attempts: int) -> RecoveryAction:
        action = decide_recovery(classification, attempts)
        self.trace(
            "recovery_decision",
            {
                "step_id": step_id,
                "classification": classification,
                "attempts": attempts,
                "action": action.value,
            },
        )
        return action

    # ------------------------------------------------------------------ #
    # Queries used by tools (via RunContext) and the final report
    # ------------------------------------------------------------------ #
    def step_summaries(self) -> list[dict[str, Any]]:
        return self.store.step_rows()

    def provenance_lines(self, name: str) -> list[str]:
        arts = self.store.artifacts_named(name, valid_only=True)
        if not arts:
            return []
        return render_lineage(self.store, arts[-1].artifact_id)

    def artifacts_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "artifact_id": a.artifact_id,
                "name": a.name,
                "kind": a.kind,
                "path": a.path,
                "valid": a.valid,
            }
            for a in self.store.all_artifacts(valid_only=False)
        ]

    def close(self) -> None:
        self.store.close()
