"""LLM planner (subtopic A, live mode).

Asks an OpenAI-compatible model to decompose the goal into a task graph,
*grounded* in the real data profiles and the real tool catalog, then
validates the model's answer strictly:

  * every tool must exist in the registry
  * step ids unique, dependencies must exist and be acyclic
  * artifact references must be produced by upstream steps

On any validation error — or when no live LLM is configured — the planner
degrades gracefully to the deterministic mock planner, so the system
always produces an executable plan.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from agent.core.context import DataContext
from agent.core.types import Step, TaskGraph
from agent.llm.client import LLMClient
from agent.planning.base import BasePlanner
from agent.planning.mock_planner import MockPlanner

_SYSTEM_PROMPT = (
    "You are a task planner for a data-analysis agent. "
    "Decompose the user's analytical goal into a small task graph. "
    "Use ONLY the tools from the provided catalog and ONLY the provided data "
    "sources. Answer with a single JSON object, no prose, no code fences, in "
    "the exact shape: "
    '{"goal": str, "steps": [{"step_id": str, "description": str, "tool": str, '
    '"args": {}, "depends_on": [str], "verify": ["schema"] or ["schema", '
    '"semantic"], "output_name": str}]}. '
    "Artifact args (e.g. \"input\") must reference the output_name of steps "
    "listed in depends_on. Honor the explicit task_contracts, including their "
    "required artifact names, filters, grouping and metrics. Keep plans minimal: usually 3-6 steps."
)


def _tool_catalog() -> list[dict[str, Any]]:
    from agent.tools.base import REGISTRY

    return [
        {
            "name": t.name,
            "description": t.description,
            "input_contract": t.input_contract,
            "output_contract": t.output_contract,
        }
        for t in REGISTRY.values()
    ]


class LLMPlanner(BasePlanner):
    label = "llm"

    def __init__(self, client: LLMClient | None = None,
                 fallback: MockPlanner | None = None):
        self.client = client or LLMClient()
        self.fallback = fallback or MockPlanner()

    # ------------------------------------------------------------------ #
    def plan(self, goal: str, ctx: DataContext) -> TaskGraph:
        if not self.client.is_live:
            print(
                "[llm-planner] no live LLM configured — "
                "falling back to the deterministic mock planner",
                file=sys.stderr,
            )
            return self.fallback.plan(goal, ctx)

        try:
            graph = self._ask(goal, ctx)
            self._validate(graph)
            return graph
        except Exception as exc:  # noqa: BLE001 — degrade, never crash
            print(
                f"[llm-planner] planning failed ({exc}) — "
                "falling back to the deterministic mock planner",
                file=sys.stderr,
            )
            return self.fallback.plan(goal, ctx)

    # ------------------------------------------------------------------ #
    def _ask(self, goal: str, ctx: DataContext) -> TaskGraph:
        user = json.dumps(
            {
                "goal": goal,
                "task_contracts": ctx.task_contracts,
                "data_sources": [
                    {"name": p.name, "path": p.path, "kind": p.kind, "schema": p.schema,
                     "issues": p.issues}
                    for p in ctx.profiles
                ],
                "tools": _tool_catalog(),
            },
            ensure_ascii=False,
        )
        raw = self.client.complete(_SYSTEM_PROMPT, user)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        data = json.loads(text)
        return TaskGraph(
            goal=data.get("goal") or goal,
            steps=[Step.from_dict(s) for s in data.get("steps", [])],
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate(graph: TaskGraph) -> None:
        from agent.tools.base import REGISTRY

        ids = [s.step_id for s in graph.steps]
        if not ids:
            raise ValueError("plan has no steps")
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step ids in plan")

        known = set(ids)
        # pass 1: artifact name -> producer step
        outputs: dict[str, str] = {
            s.output_name: s.step_id for s in graph.steps if s.output_name
        }
        # pass 2: tools, deps, and artifact references
        for s in graph.steps:
            if s.tool not in REGISTRY:
                raise ValueError(f"step {s.step_id}: unknown tool {s.tool!r}")
            for dep in s.depends_on:
                if dep not in known:
                    raise ValueError(
                        f"step {s.step_id}: unknown dependency {dep!r}"
                    )
            tool = REGISTRY[s.tool]
            for key in tool.artifact_args:
                if key in s.args:
                    refs = s.args[key]
                    refs = refs if isinstance(refs, list) else [refs]
                    for r in refs:
                        if r not in outputs:
                            raise ValueError(
                                f"step {s.step_id}: references artifact "
                                f"{r!r} that no step produces"
                            )
                        if outputs[r] not in s.depends_on:
                            raise ValueError(
                                f"step {s.step_id}: references artifact {r!r} "
                                f"(produced by {outputs[r]!r}) but does not "
                                f"depend on its producer"
                            )

        # acyclicity (Kahn)
        indeg = {i: 0 for i in ids}
        adj: dict[str, list[str]] = {i: [] for i in ids}
        for s in graph.steps:
            for dep in s.depends_on:
                adj[dep].append(s.step_id)
                indeg[s.step_id] += 1
        queue = [i for i in ids if indeg[i] == 0]
        seen = 0
        while queue:
            node = queue.pop()
            seen += 1
            for nxt in adj[node]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)
        if seen != len(ids):
            raise ValueError("plan contains a dependency cycle")
