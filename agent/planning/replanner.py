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
               state: "StateManager", feedback: dict | None = None) -> TaskGraph | None:
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


class FeedbackReplanner(Replanner):
    """Repair only the failed step's parameters; preserve the DAG and artifacts."""

    def __init__(self, client):
        self.client = client

    def replan(self, goal, graph, failed_step, state, feedback=None):
        import json
        from agent.tools.base import REGISTRY

        tool = REGISTRY[failed_step.tool]
        if failed_step.tool not in ("chart", "aggregate", "aggregate_pivot", "sql_query"):
            state.trace("repair_rejected", {"step_id": failed_step.step_id,
                        "error": "parameter repair is supported only for chart, aggregation and SQL"})
            return None
        inputs = []
        for key in tool.artifact_args:
            refs = failed_step.args.get(key, [])
            for ref in refs if isinstance(refs, list) else [refs]:
                try:
                    art = state.lookup(ref)
                except KeyError as exc:
                    state.trace("repair_rejected", {"step_id": failed_step.step_id, "error": str(exc)})
                    return None
                inputs.append({"name": art.name, "meta": art.meta})
        request = {"goal": goal, "step": failed_step.to_dict(),
                   "feedback": feedback, "inputs": inputs,
                   "input_contract": tool.input_contract}
        state.trace("repair_requested", request)
        try:
            raw = self.client.complete(
                'Repair the failed data-analysis tool arguments using the error and actual input schema. '
                'Return only a JSON object {"args": {...}}. Preserve artifact references, name and all argument keys. '
                'Do not change the requested analytical meaning. Correct only erroneous parameter values. '
                'If the schema does not support a justified repair, return {"args": null}.',
                json.dumps(request, ensure_ascii=False),
            )
            args = json.loads(raw)["args"]
            before = failed_step.args
            if not isinstance(args, dict) or set(args) != set(before) or args == before:
                raise ValueError("repair must change values while preserving argument keys")
            for key in ["name", "source", *tool.artifact_args]:
                if args.get(key) != before.get(key):
                    raise ValueError(f"repair cannot change {key}")
            columns = {c["name"] for item in inputs for c in item["meta"].get("columns", [])}
            if failed_step.tool == "chart":
                for key in ("x", "y"):
                    if not isinstance(args.get(key), str) or args[key] not in columns:
                        raise ValueError(f"unknown chart column: {key}")
            elif failed_step.tool == "sql_query":
                from agent.tools.builtins.sql_query import assert_read_only
                if not isinstance(args.get("query"), str):
                    raise ValueError("query must be a string")
                assert_read_only(args["query"])
            else:
                group = args.get("group_by")
                if not isinstance(group, str) or (group != "month" and group not in columns):
                    raise ValueError("unknown grouping column")
                if args.get("date_column") is not None and args["date_column"] not in columns:
                    raise ValueError("unknown date column")
                values = args.get("value_columns")
                if not isinstance(values, list) or not values or any(not isinstance(v, str) or v not in columns for v in values):
                    raise ValueError("unknown aggregation value columns")
            failed_step.args = args
            graph.mark(failed_step.step_id, StepStatus.PENDING)
            state.trace("parameter_repair", {"step_id": failed_step.step_id,
                        "before": before, "after": args, "feedback": feedback})
            return graph
        except Exception as exc:
            error = str(exc)
            secret = getattr(self.client, "api_key", "")
            if secret:
                error = error.replace(secret, "[REDACTED]")
            state.trace("repair_rejected", {"step_id": failed_step.step_id, "error": error})
            return None
