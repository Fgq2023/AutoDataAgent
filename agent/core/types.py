"""Shared data contracts for AutoDataAgent.

This module is the vocabulary spoken between the three subtopic modules:

  * the **planner** (subtopic A) produces a :class:`TaskGraph`
  * the **state manager** (subtopic B) tracks :class:`Step` progress,
    stores :class:`Artifact` instances and their :class:`ProvenanceRecord`
    lineage, and decides :class:`RecoveryAction` values on failure
  * the **executor / verifier** (subtopic C) produces :class:`StepResult`
    values and a :class:`Verdict` for each executed step

Any change to this file affects all three owners and must be reviewed by
everyone (see ``.github/CODEOWNERS``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable


# --------------------------------------------------------------------------- #
# Status enums
# --------------------------------------------------------------------------- #
class StepStatus(str, Enum):
    """Lifecycle of a single step inside a task graph."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    RECOVERED = "recovered"  # succeeded only after retry / replan


class RecoveryAction(str, Enum):
    """What the state manager (subtopic B) decides to do after a failure."""

    RETRY = "retry"          # re-execute the same step, same args
    REPLAN = "replan"        # ask the planner (A) for an alternative step
    ROLLBACK = "rollback"    # invalidate artifacts after a checkpoint
    SKIP = "skip"            # give up on the step, keep the rest running
    FAIL = "fail"            # give up on the step, cascade-skip dependents


# --------------------------------------------------------------------------- #
# Plan (produced by subtopic A)
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    """One executable unit of a plan."""

    step_id: str
    description: str
    tool: str                       # key into the tool registry (subtopic C)
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=lambda: ["schema"])
    output_name: str = ""           # logical artifact name produced

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Step":
        return Step(
            step_id=d["step_id"],
            description=d.get("description", ""),
            tool=d["tool"],
            args=d.get("args", {}) or {},
            depends_on=list(d.get("depends_on", []) or []),
            verify=list(d.get("verify", ["schema"]) or ["schema"]),
            output_name=d.get("output_name", "") or "",
        )


@dataclass
class TaskGraph:
    """A decomposed task: an ordered DAG of :class:`Step` values."""

    goal: str
    steps: list[Step] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Runtime-only scheduling status, deliberately excluded from
        # Step.to_dict() so serialised graphs always restart as PENDING.
        for s in self.steps:
            if not hasattr(s, "_status"):
                setattr(s, "_status", StepStatus.PENDING)

    # -- lookups ------------------------------------------------------------
    def get(self, step_id: str) -> Step:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        raise KeyError(f"unknown step_id: {step_id}")

    def ids(self) -> list[str]:
        return [s.step_id for s in self.steps]

    # -- scheduling ----------------------------------------------------------
    def next_ready(self) -> Step | None:
        """Return a PENDING step whose dependencies all finished OK.

        ``SUCCEEDED`` and ``RECOVERED`` both count as finished OK.
        """
        for s in self.steps:
            if s._status != StepStatus.PENDING:  # type: ignore[attr-defined]
                continue
            deps_ok = all(
                self.get(dep)._status  # type: ignore[attr-defined]
                in (StepStatus.SUCCEEDED, StepStatus.RECOVERED)
                for dep in s.depends_on
            )
            if deps_ok:
                return s
        return None

    def done(self) -> bool:
        """True when no step is still PENDING or RUNNING."""
        return all(
            s._status not in (StepStatus.PENDING, StepStatus.RUNNING)  # type: ignore[attr-defined]
            for s in self.steps
        )

    # -- mutation ------------------------------------------------------------
    def mark(self, step_id: str, status: StepStatus) -> None:
        self.get(step_id)._status = status  # type: ignore[attr-defined]

    def status_of(self, step_id: str) -> StepStatus:
        return self.get(step_id)._status  # type: ignore[attr-defined]

    def cascade_skip(self, failed_id: str) -> list[str]:
        """Mark every PENDING step that transitively depends on a failed step
        as SKIPPED.  Returns the ids that were skipped."""
        blocked = {failed_id}
        changed = True
        while changed:
            changed = False
            for s in self.steps:
                if s._status == StepStatus.PENDING and any(  # type: ignore[attr-defined]
                    dep in blocked for dep in s.depends_on
                ):
                    self.mark(s.step_id, StepStatus.SKIPPED)
                    blocked.add(s.step_id)
                    changed = True
        skipped = [
            s.step_id for s in self.steps if s._status == StepStatus.SKIPPED
        ]  # type: ignore[attr-defined]
        return skipped

    # -- serialisation --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"goal": self.goal, "steps": [s.to_dict() for s in self.steps]}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TaskGraph":
        return TaskGraph(
            goal=d["goal"],
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
        )


# --------------------------------------------------------------------------- #
# Data-source context (shared, built by core from the data root)
# --------------------------------------------------------------------------- #
@dataclass
class DataProfile:
    """A profile of one data source found under the data root."""

    name: str
    path: str                      # absolute path
    kind: str                      # csv | xlsx | json | sqlite
    schema: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


@dataclass
class DataContext:
    """All data-source profiles visible to a run."""

    root: str
    profiles: list[DataProfile] = field(default_factory=list)
    task_contracts: list[dict[str, Any]] = field(default_factory=list)

    def tabular_profiles(self) -> list[DataProfile]:
        return [p for p in self.profiles if p.kind in ("csv", "xlsx")]

    def sqlite_profiles(self) -> list[DataProfile]:
        return [p for p in self.profiles if p.kind == "sqlite"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "task_contracts": self.task_contracts,
            "profiles": [asdict(p) for p in self.profiles],
        }


# --------------------------------------------------------------------------- #
# Execution (produced by subtopic C, tracked by subtopic B)
# --------------------------------------------------------------------------- #
@dataclass
class Artifact:
    """A durable intermediate product of a run (file + metadata)."""

    artifact_id: str               # unique within the run, e.g. "clean_sales"
    name: str                      # logical name referenced in step args
    kind: str                      # dataframe | table | figure | text
    path: str                      # path relative to the run directory
    meta: dict[str, Any] = field(default_factory=dict)
    valid: bool = True             # False once rolled back / invalidated
    created_at: str = ""


@dataclass
class StepResult:
    """Outcome of one execution attempt of a step."""

    step_id: str
    status: StepStatus = StepStatus.SUCCEEDED
    outputs: list[str] = field(default_factory=list)   # artifact ids
    error: str | None = None
    duration_ms: int = 0
    attempts: int = 1
    classification: str = "execution"  # timeout | dependency | execution | verify
    verdict: "Verdict | None" = None   # attached when verification ran


@dataclass
class CheckResult:
    """One named verification check."""

    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Verdict:
    """Aggregated verification verdict for one step attempt."""

    ok: bool
    checks: list[CheckResult] = field(default_factory=list)

    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [c.to_dict() for c in self.checks]}


@dataclass
class ProvenanceRecord:
    """Lineage record: which step/tool produced an artifact from which inputs."""

    artifact_id: str
    step_id: str
    tool: str
    input_artifact_ids: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Run context handed to tools at execution time
# --------------------------------------------------------------------------- #
@dataclass
class RunContext:
    """Everything a tool may need about the current run.

    Built by the executor from the state manager so tools stay decoupled
    from subtopic B's internals.
    """

    run_dir: str                                    # absolute run directory
    goal: str
    lookup: Callable[[str], Artifact]                # artifact name -> Artifact
    step_summaries: Callable[[], list[dict[str, Any]]]
    provenance_lines: Callable[[str], list[str]]    # lineage lines for a name
    timeout_s: float = 120.0                         # sandbox wall-clock budget


# --------------------------------------------------------------------------- #
# Run summary (produced by the orchestrator at the end)
# --------------------------------------------------------------------------- #
@dataclass
class RunSummary:
    goal: str
    run_dir: str
    run_id: str
    status: str                     # finished | finished_with_failures
    n_steps: int
    n_succeeded: int
    n_recovered: int
    n_failed: int
    n_skipped: int
    n_retries: int
    n_replans: int
    duration_ms: int
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    report_path: str | None = None
    trace_path: str | None = None
    planner: str = "mock"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
