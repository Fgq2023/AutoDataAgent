"""Dynamic replanning (subtopic A).

When the recovery policy (subtopic B) decides REPLAN, the replanner looks
for a *tool-level alternative* with an identical contract and swaps it in,
resetting the step to PENDING.  If no alternative exists, the step fails
for good and the orchestrator cascade-skips its dependents.

This is the v0.1 replanning strategy (tool substitution).  LLM-driven
replanning (regenerating the whole downstream graph with the failure as
context) is the main research direction for subtopic A.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.core.types import StepStatus, TaskGraph

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.types import Step
    from agent.state.manager import StateManager


ALTERNATIVES: dict[str, str] = {
    "aggregate": "aggregate_pivot",
    "aggregate_pivot": "aggregate",
}


class Replanner:
    label = "rule-based-replanner"

    def replan(self, goal: str, graph: TaskGraph, failed_step: "Step",
               state: "StateManager") -> TaskGraph | None:
        """Try to rescue *failed_step* by swapping in an alternative tool.

        Returns the updated graph, or ``None`` when no alternative exists.
        """
        alternative = ALTERNATIVES.get(failed_step.tool)
        if not alternative or alternative == failed_step.tool:
            return None

        old_tool = failed_step.tool
        failed_step.tool = alternative
        failed_step.description = (
            f"{failed_step.description} [replanned: {old_tool} -> {alternative}]"
        )
        graph.mark(failed_step.step_id, StepStatus.PENDING)
        state.trace(
            "replan",
            {
                "step_id": failed_step.step_id,
                "from_tool": old_tool,
                "to_tool": alternative,
                "reason": "recovery policy chose REPLAN",
            },
        )
        return graph
