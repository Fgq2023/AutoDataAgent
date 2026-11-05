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
from dataclasses import asdict
from agent.state import journal
from pathlib import Path
from agent.core.task_spec import ClarificationRequired, TaskSpec
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
    def run(self, goal: str | TaskSpec, *, cancelled: Callable[[], bool] | None = None, _restored=None) -> RunSummary:
        t0 = time.perf_counter()
        client = getattr(self.planner, "client", None) or getattr(self.replanner, "client", None)

        explicit_spec = isinstance(goal, TaskSpec)
        spec = goal if explicit_spec else TaskSpec(goal)
        spec.require_answers()
        goal = spec.goal
        ctx: DataContext = profile_data_sources(self.config.data_root)
        if not ctx.profiles:
            raise ValueError(
                f"no supported data sources found under {self.config.data_root!r} "
                "— add CSV/XLSX/JSON/SQLite files first"
            )

        if explicit_spec and not spec.sources:
            data_profiles = [profile for profile in ctx.profiles if profile.kind != "text"]
            if len(data_profiles) > 1:
                names = ", ".join(Path(profile.path).relative_to(ctx.root).as_posix()
                                  for profile in data_profiles)
                raise ClarificationRequired({"sources": f"Set sources to the intended files. Available: {names}"})
        if spec.sources:
            root = Path(self.config.data_root).resolve()
            requested = {(root / source).resolve() for source in spec.sources}
            available = {Path(profile.path) for profile in ctx.profiles}
            if any(Path(source).is_absolute() for source in spec.sources) or not requested <= available:
                raise ValueError("TaskSpec sources must identify profiled files relative to data-root")
            ctx.profiles = [profile for profile in ctx.profiles if Path(profile.path) in requested]
        citations = spec.rule_citations(self.config.data_root)
        ctx.task_spec = {**spec.to_dict(), "rule_citations": citations}
        ctx.task_contracts = self.config.task_contracts
        state = _restored[0] if _restored else self.state_factory(
            self.config.outputs_root, goal, TaskGraph(goal, []))
        manifest = _restored[1] if _restored else {
            "version": 1, "phase": "planning", "spec": spec.to_dict(), "explicit_spec": explicit_spec,
            "config": asdict(self.config), "plan": state.graph.to_dict(),
            "inputs": {profile.path: journal.digest(profile.path) for profile in ctx.profiles},
            "elapsed": 0.0, "active_since": time.time(), "model_requests": 0,
            "model_identity": {key: getattr(client, key) for key in
                               ("provider", "base_url", "model", "session_id")
                               if hasattr(client, key)},
        }
        if not _restored:
            journal.initialize(state, manifest)
        planning_hook = getattr(client, "before_request", None)
        planning_events = getattr(client, "on_event", None)

        def reserve_planning_request():
            remaining = self.config.run_timeout_s - (time.perf_counter() - t0)
            if remaining <= 0 or manifest["model_requests"] >= 8:
                raise ValueError("planning budget exhausted")
            manifest["model_requests"] += 1
            manifest["elapsed"] = time.perf_counter() - t0
            manifest["active_since"] = time.time()
            journal.save(state, manifest)
            return min(60, remaining)

        if client is not None and not _restored:
            client.before_request = reserve_planning_request
            client.on_event = state.trace
        try:
            graph = _restored[0].graph if _restored else self.planner.plan(goal, ctx)
            from agent.verification.task import validate_plan_contracts

            validate_plan_contracts(graph, self.config.task_contracts)
            for step in graph.steps:
                if spec.sources and "source" in self.registry[step.tool].input_contract:
                    source = step.args.get("source")
                    if not isinstance(source, str) or Path(source).resolve() not in requested:
                        raise ValueError(f"step {step.step_id}: source outside TaskSpec")
            for artifact, columns in spec.output_columns.items():
                producers = [step for step in graph.steps if step.output_name == artifact]
                if len(producers) != 1:
                    raise ValueError(f"TaskSpec requires exactly one producer for {artifact!r}")
                step = producers[0]
                if step.output_columns and step.output_columns != columns:
                    raise ValueError(f"step {step.step_id}: output_columns conflicts with TaskSpec")
                if self.registry[step.tool].output_contract.get("kind") not in ("table", "dataframe"):
                    raise ValueError(f"step {step.step_id}: TaskSpec requires a table output")
                step.output_columns = list(columns)
            validate_plan_contracts(graph, self.config.task_contracts)
            if not _restored:
                state.graph = graph
                state.store.insert_steps(graph.steps)
                manifest["plan"] = graph.to_dict()
                manifest["phase"] = "execution"
                journal.save(state, manifest)
        except BaseException as exc:
            if not _restored:
                manifest["phase"] = "planning_failed"
                manifest["elapsed"] = time.perf_counter() - t0
                manifest["active_since"] = None
                journal.save(state, manifest)
                exc.run_dir = str(state.run_dir)
                state.trace("run_stopped", {"phase": "planning",
                            "reason": getattr(exc, "reason", "planning_error")})
                state.export_trace()
                state.close()
            raise
        finally:
            if client is not None and not _restored:
                client.before_request = planning_hook
                client.on_event = planning_events
        from agent.state.lock import run_lock
        guard = None
        if not _restored:
            guard = run_lock(state.run_dir)
            guard.__enter__()
        state.trace(
            "run_started",
            {
                "goal": goal,
                "planner": getattr(self.planner, "label", type(self.planner).__name__),
                "mode": self.config.mode,
                "data_root": str(self.config.data_root),
                "profiles": [p.name for p in ctx.profiles],
                "task_spec": ctx.task_spec,
                "task_contracts": self.config.task_contracts,
            },
        )
        state.trace("plan_created", {"steps": graph.to_dict()["steps"]})

        injector = (
            FailureInjector(self.config.inject_failure)
            if self.config.inject_failure
            else None
        )
        executor = Executor(self.registry, state, self.config, injector, rule_citations=citations)
        from agent.verification.verifier import Verifier  # local: avoid import cycle

        verifier = Verifier(self.registry, self.config.task_contracts)

        total_attempts: dict[str, int] = {}
        for event in state.store.all_trace():
            if event["event"] == "step_attempt":
                sid = event["payload"]["step_id"]
                total_attempts[sid] = total_attempts.get(sid, 0) + 1
        attempts = dict(total_attempts)
        had_failure: set[str] = set()  # steps rescued by retry / replan
        n_retries = sum(event["event"] == "retry_started" for event in state.store.all_trace())
        n_replans = sum(event["event"] == "replan_started" for event in state.store.all_trace())
        elapsed_before = manifest["elapsed"] if _restored else 0.0
        manifest["active_since"] = time.time()
        journal.save(state, manifest)

        def persist():
            manifest["plan"] = graph.to_dict()
            manifest["elapsed"] = elapsed_before + time.perf_counter() - t0
            manifest["active_since"] = time.time()
            journal.save(state, manifest)
        stop_reason = None

        clients = {c for c in (client, getattr(self.replanner, "client", None)) if c is not None}
        old_hooks = {c: getattr(c, "before_request", None) for c in clients}
        old_events = {c: getattr(c, "on_event", None) for c in clients}

        def before_request():
            if elapsed_before + time.perf_counter() - t0 >= self.config.run_timeout_s:
                raise ValueError("run time budget exhausted")
            if manifest["model_requests"] >= 8:
                raise ValueError("model request budget exhausted")
            manifest["model_requests"] += 1
            state.trace("model_request", {"count": manifest["model_requests"]})
            persist()
            return max(0.001, min(60, self.config.run_timeout_s - elapsed_before - (time.perf_counter() - t0)))

        for model_client in clients:
            model_client.before_request = before_request
            model_client.on_event = state.trace

        def check_stop() -> str | None:
            if cancelled is not None and cancelled():
                return "cancelled"
            if elapsed_before + time.perf_counter() - t0 >= self.config.run_timeout_s:
                return "run_timeout"
            if sum(total_attempts.values()) >= self.config.max_tool_attempts:
                return "max_tool_attempts"
            return None

        try:
            while not graph.done() and stop_reason is None:
                step: Step | None = graph.next_ready()
                if step is None:  # defensive: all remaining steps are blocked
                    state.trace("deadlock_detected", {"remaining": graph.ids()})
                    break

                state.mark(step.step_id, StepStatus.RUNNING)

                while True:
                    stop_reason = check_stop()
                    if not stop_reason and total_attempts.get(step.step_id, 0) >= self.config.max_attempts:
                        stop_reason = "max_attempts"
                    if stop_reason:
                        break
                    attempts[step.step_id] = attempts.get(step.step_id, 0) + 1
                    attempt_no = attempts[step.step_id]
                    total_attempts[step.step_id] = total_attempts.get(step.step_id, 0) + 1
                    state.trace(
                        "step_attempt",
                        {"step_id": step.step_id, "tool": step.tool, "attempt": attempt_no},
                    )

                    persist()
                    executor.remaining_s = max(0.001, self.config.run_timeout_s -
                                               elapsed_before - (time.perf_counter() - t0))
                    result = self._attempt(
                        step, attempt_no, executor, verifier, state, graph
                    )

                    if result.status == StepStatus.SUCCEEDED:
                        result.attempts = total_attempts[step.step_id]
                        final_status = (
                            StepStatus.SUCCEEDED
                            if attempt_no == 1 and step.step_id not in had_failure
                            else StepStatus.RECOVERED
                        )
                        state.checkpoint(step, result, result.verdict, final_status)
                        graph.mark(step.step_id, final_status)
                        persist()
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
                    feedback = {"step_id": step.step_id, "tool": step.tool,
                                "classification": result.classification,
                                "error": result.error,
                                "attempts": total_attempts[step.step_id],
                                "verdict": result.verdict.to_dict() if result.verdict else None}
                    state.trace("failure_feedback", feedback)
                    if total_attempts[step.step_id] >= self.config.max_attempts:
                        action = RecoveryAction.FAIL
                        state.trace("budget_exhausted", {"step_id": step.step_id, "budget": "max_attempts"})
                    if action == RecoveryAction.REPLAN and n_replans >= self.config.max_replans:
                        action = RecoveryAction.FAIL
                        state.trace("budget_exhausted", {"step_id": step.step_id, "budget": "max_replans"})

                    stop_reason = check_stop()
                    if stop_reason:
                        state.mark(step.step_id, StepStatus.FAILED, error=result.error,
                                   attempts=total_attempts[step.step_id])
                        graph.mark(step.step_id, StepStatus.FAILED)
                        break

                    if action == RecoveryAction.RETRY:
                        n_retries += 1
                        state.trace("retry_started", {"step_id": step.step_id})
                        continue  # same step, next attempt

                    if action == RecoveryAction.REPLAN:
                        n_replans += 1
                        state.trace("replan_started", {"step_id": step.step_id})
                        new_graph = self.replanner.replan(goal, graph, step, state, feedback=feedback)
                        if new_graph is not None:
                            if new_graph is not graph:
                                raise ValueError("local repair must preserve the existing task graph")
                            persist()
                            attempts[step.step_id] = 0  # fresh start, new tool
                            break  # back to the outer loop (next_ready again)
                        action = RecoveryAction.FAIL  # no alternative existed

                    # FAIL / SKIP: terminal
                    state.mark(
                        step.step_id,
                        StepStatus.FAILED,
                        error=result.error,
                        attempts=total_attempts[step.step_id],
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

            if stop_reason:
                state.trace("run_stopped", {"reason": stop_reason,
                                            "tool_attempts": sum(total_attempts.values())})
                for step in graph.steps:
                    if graph.status_of(step.step_id) in (StepStatus.PENDING, StepStatus.RUNNING):
                        state.mark(step.step_id, StepStatus.SKIPPED, error=stop_reason)
                        graph.mark(step.step_id, StepStatus.SKIPPED)

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
        except Exception as exc:
            from agent.llm.client import LLMProviderStop
            if isinstance(exc, LLMProviderStop):
                exc.run_dir = str(state.run_dir)
                state.trace("run_stopped", {"phase": "execution", "reason": exc.reason, "status": exc.status})
            raise
        finally:
            for model_client, hook in old_hooks.items():
                model_client.before_request = hook
                model_client.on_event = old_events[model_client]
            persist()
            manifest["active_since"] = None
            journal.save(state, manifest)
            state.export_trace()
            state.close()
            if guard is not None:
                guard.__exit__(None, None, None)

    def resume(self, run_dir, *, cancelled=None):
        from agent.state.lock import run_lock
        with run_lock(run_dir):
            return self._resume(run_dir, cancelled=cancelled)

    def _resume(self, run_dir, *, cancelled=None):
        from agent.core.config import AgentConfig
        from agent.core.types import Step
        from agent.state.manager import StateManager
        from agent.state.store import RunStore

        run_dir = Path(run_dir).resolve()
        if not (run_dir / "state.db").is_file():
            raise ValueError("run state database not found")
        store = RunStore(run_dir / "state.db")
        try:
            manifest = journal.load(store)
            if manifest.get("version") != 1:
                raise ValueError("unsupported resume version")
            if manifest.get("phase", "execution") != "execution":
                raise ValueError("initial planning incomplete; inspect trace and start a new run")
            for path, expected in manifest["inputs"].items():
                if not Path(path).is_file() or journal.digest(path) != expected:
                    raise ValueError(f"input changed: {path}")
            plan = manifest["plan"]
            graph = TaskGraph(plan["goal"], [Step.from_dict(step) for step in plan["steps"]])
            meta = store.conn.execute("SELECT * FROM runs").fetchone()
            state = StateManager(run_dir, store, meta["run_id"], meta["goal"], graph)
            for row in store.step_rows():
                status = StepStatus(row["status"])
                if status in (StepStatus.SUCCEEDED, StepStatus.RECOVERED):
                    for art in store.artifacts_of_step(row["step_id"], valid_only=True):
                        if not (run_dir / art.path).is_file() or journal.digest(run_dir / art.path) != art.meta.get("sha256"):
                            raise ValueError(f"artifact changed: {art.name}")
                    graph.mark(row["step_id"], status)
            with store.transaction():
                for row in store.step_rows():
                    if StepStatus(row["status"]) not in (StepStatus.SUCCEEDED, StepStatus.RECOVERED):
                        state.invalidate_step_artifacts(row["step_id"])
                        state.mark(row["step_id"], StepStatus.PENDING)
            if manifest["active_since"] is not None:
                manifest["elapsed"] += max(0, time.time() - manifest["active_since"])
                state.trace("interrupted_attempt_detected")
            self.config = AgentConfig(**manifest["config"])
            identity = manifest.get("model_identity", {})
            for model_client in (getattr(self.planner, "client", None),
                                 getattr(self.replanner, "client", None)):
                if model_client is not None:
                    for key, value in identity.items():
                        if key in ("provider", "base_url", "model", "session_id"):
                            setattr(model_client, key, value)
            state.trace("run_resumed", {"elapsed": manifest["elapsed"]})
        except BaseException:
            store.close()
            raise
        try:
            spec = TaskSpec(**manifest["spec"])
            return self.run(spec if manifest["explicit_spec"] else spec.goal,
                            cancelled=cancelled, _restored=(state, manifest))
        finally:
            store.close()

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
            verdict=verdict,
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
                if counts[StepStatus.SUCCEEDED] + counts[StepStatus.RECOVERED] == len(graph.steps)
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
