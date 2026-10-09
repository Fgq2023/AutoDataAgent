"""Step executor and failure injector (subtopic C).

The executor turns one :class:`~agent.core.types.Step` into a
:class:`~agent.core.types.StepResult`:

  1. resolve artifact references in the step's args (via the state manager)
  2. build the :class:`~agent.core.types.RunContext` for the tool
  3. run the tool
  4. register produced artifacts + provenance with the state manager

Failures raise :class:`ToolExecutionError` carrying a *classification*
from the recovery taxonomy (subtopic B) so the orchestrator can act on it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent.core.types import Artifact, RunContext, Step, StepResult, StepStatus
from agent.tools.sandbox import SandboxTimeout

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.config import AgentConfig
    from agent.state.manager import StateManager
    from agent.tools.base import Tool


class ToolExecutionError(Exception):
    """A step attempt failed, with a recovery classification attached."""

    def __init__(self, message: str, classification: str = "execution"):
        super().__init__(message)
        self.classification = classification


class FailureInjector:
    """Deterministic failure injection for demos and tests.

    Specs (``--inject-failure``):

      * ``s3``           — transient: step s3 fails on its first attempt
                           only (classified as ``timeout``)
      * ``s3:permanent`` — the *original tool* of step s3 fails on every
                           attempt; after a replan swaps the tool, the
                           injection no longer applies (classified as
                           ``execution``)
    """

    def __init__(self, spec: str):
        self.target, _, mode = spec.partition(":")
        self.mode = mode or "transient"
        if self.mode not in ("transient", "permanent"):
            raise ValueError(f"unknown injection mode: {self.mode}")
        self._armed_tool: str | None = None

    def check(self, step_id: str, tool: str, attempt: int) -> tuple[str, str] | None:
        """Return ``(message, classification)`` if this attempt must fail."""
        if step_id != self.target:
            return None
        if self.mode == "transient":
            if attempt == 1:
                return (
                    f"[injected] transient timeout at step {step_id} "
                    f"(tool {tool}, attempt 1)",
                    "timeout",
                )
            return None
        # permanent: fail only while the step still runs its original tool
        if self._armed_tool is None:
            self._armed_tool = tool
        if tool == self._armed_tool:
            return (
                f"[injected] permanent failure at step {step_id} (tool {tool})",
                "execution",
            )
        return None


class Executor:
    def __init__(self, registry, state: "StateManager",
                 config: "AgentConfig", injector: FailureInjector | None = None):
        self.registry = registry
        self.state = state
        self.config = config
        self.injector = injector

    # ------------------------------------------------------------------ #
    def execute(self, step: Step, attempt: int) -> StepResult:
        t0 = time.perf_counter()
        if self.injector is not None:
            hit = self.injector.check(step.step_id, step.tool, attempt)
            if hit is not None:
                message, classification = hit
                self.state.trace(
                    "failure_injected",
                    {"step_id": step.step_id, "attempt": attempt, "message": message},
                )
                raise ToolExecutionError(message, classification)

        try:
            tool: "Tool" = self.registry[step.tool]
        except KeyError as exc:
            raise ToolExecutionError(
                f"no such tool: {step.tool!r} (registered: {sorted(self.registry)})",
                "dependency",
            ) from exc

        try:
            resolved, input_ids = self._resolve_args(step, tool)
            ctx = RunContext(
                run_dir=str(self.state.run_dir.resolve()),
                goal=self.state.goal,
                lookup=self.state.lookup,
                step_summaries=self.state.step_summaries,
                provenance_lines=self.state.provenance_lines,
                timeout_s=self.config.exec_timeout_s,
            )
            specs = tool.run(resolved, ctx)
            artifacts = self.state.register_artifacts(step, specs, input_ids)
        except ToolExecutionError:
            raise
        except SandboxTimeout as exc:
            raise ToolExecutionError(str(exc), "timeout") from exc
        except Exception as exc:  # noqa: BLE001 — classify, let B decide
            raise ToolExecutionError(
                f"{type(exc).__name__}: {exc}", "execution"
            ) from exc

        return StepResult(
            step_id=step.step_id,
            status=StepStatus.SUCCEEDED,
            outputs=[a.artifact_id for a in artifacts],
            duration_ms=int((time.perf_counter() - t0) * 1000),
            attempts=attempt,
        )

    # ------------------------------------------------------------------ #
    def _resolve_args(self, step: Step, tool: "Tool") -> tuple[dict[str, Any], list[str]]:
        """Replace artifact-name references with live Artifact objects.

        Returns the resolved args plus the ids of all input artifacts
        (for provenance).
        """
        resolved: dict[str, Any] = dict(step.args)
        input_ids: list[str] = []

        for key in tool.artifact_args:
            if key not in resolved:
                continue
            value = resolved[key]
            if isinstance(value, list):
                arts = [self.state.lookup(v) for v in value]
                resolved[key] = arts
                input_ids.extend(a.artifact_id for a in arts)
            else:
                art = self.state.lookup(value)
                resolved[key] = art
                input_ids.append(art.artifact_id)

        return resolved, input_ids


def artifact_abs_path(run_dir: str | Path, art: Artifact) -> Path:
    """Absolute filesystem path of an artifact (helper for tools)."""
    return Path(run_dir) / art.path
