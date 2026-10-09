"""The orchestrator — the agent's main control loop.

Wires the three subtopic modules into one reliable workflow::

    profile data sources
          │
    plan (A) ─────────────┐
          │                │ replan on failure (A)
          ▼                │
    execute (C) ── fail ────┤
          │                │
          ▼                ▼
    verify (C) ── fail ─► recovery policy (B) ─► RETRY / REPLAN / FAIL
          │
       succeed
          │
          ▼
    checkpoint + provenance (B)
          │
          ▼
    next step ... ─► report + trace export

Every decision is appended to the run's trace (``trace.json``) and every
artifact carries a provenance record (``state.db``).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from agent.core.context import profile_data_sources
from agent.core.types import (
    RecoveryAction,
    RunSummary,
    StepResult,
    StepStatus,
    TaskGraph,
    Verdict,
)
from agent.tools.executor import Executor, FailureInjector, ToolExecutionError

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.config import AgentConfig
    from agent.core.context import DataContext
    from agent.core.types import Step
    from agent.planning.base import BasePlanner
    from agent.planning.replanner import Replanner
    from agent.state.manager import StateManager
    from agent.verification.verifier import Verifier


def _verdict_error(verdict: Verdict) -> str:
    failed = verdict.failed_checks()
    return "; ".join(f"{c.name}: {c.detail}" for c in failed)


class Orchestrator:
    def __init__(
        self,
        planner: "BasePlanner",
        replanner: "Replanner",
        registry: dict,
        state_factory: Callable[[str, str, TaskGraph], "StateManager"],
        config: "AgentConfig",
    ):
        self.planner = planner
        self.replanner = replanner
        self.registry = registry
        self.state_factory = state_factory
        self.config = config

    # ------------------------------------------------------------------ #
    def run(self, goal: str) -> RunSummary:
        t0 = time.perf_counter()

        ctx: DataContext = profile_data_sources(self.config.data_root)
        if not ctx.profiles:
            raise ValueError(
                f"no supported data sources found under {self.config.data_root!r} "
                "— add CSV/XLSX/JSON/SQLite files first"
            )

        graph = self.planner.plan(goal, ctx)
        state = self.state_factory(self.config.outputs_root, goal, graph)
        state.trace(
            "run_started",
            {
                "goal": goal,
                "planner": getattr(self.planner, "label", type(self.planner).__name__),
                "mode": self.config.mode,
                "data_root": str(self.config.data_root),
                "profiles": [p.name for p in ctx.profiles],
            },
        )
        state.trace("plan_created", {"steps": graph.to_dict()["steps"]})

        injector = (
            FailureInjector(self.config.inject_failure)
            if self.config.inject_failure
            else None
        )
        executor = Executor(self.registry, state, self.config, injector)
        from agent.verification.verifier import Verifier  # local: avoid import cycle

        verifier = Verifier(self.registry)

        attempts: dict[str, int] = {}
        had_failure: set[str] = set()  # steps rescued by retry / replan
        n_retries = 0
        n_replans = 0

        try:
            while not graph.done():
                step: Step | None = graph.next_ready()
                if step is None:  # defensive: all remaining steps are blocked
                    state.trace("deadlock_detected", {"remaining": graph.ids()})
                    break

                state.mark(step.step_id, StepStatus.RUNNING)

                while True:
                    attempts[step.step_id] = attempts.get(step.step_id, 0) + 1
                    attempt_no = attempts[step.step_id]
                    state.trace(
                        "step_attempt",
                        {"step_id": step.step_id, "tool": step.tool, "attempt": attempt_no},
                    )

                    result = self._attempt(
                        step, attempt_no, executor, verifier, state, graph
                    )

                    if result.status == StepStatus.SUCCEEDED:
                        final_status = (
                            StepStatus.SUCCEEDED
                            if attempt_no == 1 and step.step_id not in had_failure
                            else StepStatus.RECOVERED
                        )
                        state.checkpoint(step, result, result.verdict, final_status)
                        graph.mark(step.step_id, final_status)
                        state.trace(
                            "step_finished",
                            {"step_id": step.step_id, "status": final_status.value},
                        )
                        break

                    # ---- failure: ask subtopic B what to do -------------------
                    had_failure.add(step.step_id)
                    action = state.decide_recovery(
                        step.step_id, result.classification, attempt_no
                    )

                    if action == RecoveryAction.RETRY:
                        n_retries += 1
                        continue  # same step, next attempt

                    if action == RecoveryAction.REPLAN:
                        n_replans += 1
                        new_graph = self.replanner.replan(goal, graph, step, state)
                        if new_graph is not None:
                            attempts[step.step_id] = 0  # fresh start, new tool
                            break  # back to the outer loop (next_ready again)
                        action = RecoveryAction.FAIL  # no alternative existed

                    # FAIL / SKIP: terminal
                    state.mark(
                        step.step_id,
                        StepStatus.FAILED,
                        error=result.error,
                        attempts=attempt_no,
                        duration_ms=result.duration_ms,
                    )
                    graph.mark(step.step_id, StepStatus.FAILED)
                    state.trace(
                        "step_finished",
                        {"step_id": step.step_id, "status": "failed",
                         "error": result.error},
                    )
                    skipped = graph.cascade_skip(step.step_id)
                    for sid in skipped:
                        state.mark(sid, StepStatus.SKIPPED)
                    if skipped:
                        state.trace("cascade_skip", {"skipped": skipped})
                    break

            summary = self._summarize(goal, state, graph, t0, n_retries, n_replans)
            state.trace(
                "run_finished",
                {
                    "status": summary.status,
                    "n_succeeded": summary.n_succeeded,
                    "n_recovered": summary.n_recovered,
                    "n_failed": summary.n_failed,
                    "n_skipped": summary.n_skipped,
                    "n_retries": n_retries,
                    "n_replans": n_replans,
                    "duration_ms": summary.duration_ms,
                },
            )
            state.export_trace()
            return summary
        finally:
            state.close()

    # ------------------------------------------------------------------ #
    def _attempt(
        self,
        step: "Step",
        attempt_no: int,
        executor: Executor,
        verifier: "Verifier",
        state: "StateManager",
        graph: TaskGraph,
    ) -> "StepResult":
        """One attempt: execute + verify, returning a SUCCEEDED (with verdict)
        or FAILED (with classification) StepResult."""

        try:
            result = executor.execute(step, attempt_no)
        except ToolExecutionError as exc:
            state.trace(
                "step_attempt_failed",
                {
                    "step_id": step.step_id,
                    "attempt": attempt_no,
                    "classification": exc.classification,
                    "error": str(exc),
                },
            )
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.FAILED,
                outputs=[],
                error=str(exc),
                duration_ms=0,
                attempts=attempt_no,
                classification=exc.classification,
            )

        verdict = verifier.verify(step, result, state)
        state.trace(
            "verification",
            {"step_id": step.step_id, "attempt": attempt_no, **verdict.to_dict()},
        )

        if verdict.ok:
            result.verdict = verdict
            return result

        # verification failed: invalidate the artifacts of this attempt,
        # classify the failure for the recovery policy
        state.invalidate_step_artifacts(step.step_id)
        error = f"verification failed: {_verdict_error(verdict)}"
        state.trace(
            "step_attempt_failed",
            {
                "step_id": step.step_id,
                "attempt": attempt_no,
                "classification": "verify",
                "error": error,
            },
        )
        return StepResult(
            step_id=step.step_id,
            status=StepStatus.FAILED,
            outputs=[],
            error=error,
            duration_ms=result.duration_ms,
            attempts=attempt_no,
            classification="verify",
        )

    # ------------------------------------------------------------------ #
    def _summarize(
        self,
        goal: str,
        state: "StateManager",
        graph: TaskGraph,
        t0: float,
        n_retries: int,
        n_replans: int,
    ) -> RunSummary:
        statuses = [
            (s.step_id, graph.status_of(s.step_id)) for s in graph.steps
        ]
        counts = {st: 0 for st in StepStatus}
        for _, st in statuses:
            counts[st] += 1

        report_path = None
        final = state.store.artifacts_named("final_report", valid_only=True)
        if final:
            report_path = str(state.run_dir / final[-1].path)

        return RunSummary(
            goal=goal,
            run_dir=str(state.run_dir),
            run_id=state.run_id,
            status=(
                "finished"
                if counts[StepStatus.FAILED] == 0 and counts[StepStatus.SKIPPED] == 0
                else "finished_with_failures"
            ),
            n_steps=len(graph.steps),
            n_succeeded=counts[StepStatus.SUCCEEDED],
            n_recovered=counts[StepStatus.RECOVERED],
            n_failed=counts[StepStatus.FAILED],
            n_skipped=counts[StepStatus.SKIPPED],
            n_retries=n_retries,
            n_replans=n_replans,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            artifacts=state.artifacts_summary(),
            report_path=report_path,
            trace_path=str(state.run_dir / "trace.json"),
            planner=getattr(self.planner, "label", type(self.planner).__name__),
        )
