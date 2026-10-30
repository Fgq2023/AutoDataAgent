"""The verifier (subtopic C) — two-layer output checking.

Layer 1 (*schema*): every output artifact is what its tool contract
promised — file exists, right kind, loads back with the recorded shape.

Layer 2 (*semantic*): the tool's declared sanity rules run against the
output *and its inputs* (e.g. "cleaning cannot add rows").

Schema runs first; if it fails, semantic checks are skipped — untrustworthy
files cannot be semantically judged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.core.types import CheckResult, Step, StepResult, Verdict
from agent.verification.schema import check_artifact
from agent.verification.semantic import run_rules

if TYPE_CHECKING:  # pragma: no cover
    from agent.state.manager import StateManager


class Verifier:
    def __init__(self, registry, task_contracts=None):
        self.registry = registry
        self.task_contracts = task_contracts or []

    def verify(self, step: Step, result: StepResult,
               state: "StateManager") -> Verdict:
        try:
            tool = self.registry[step.tool]
        except KeyError:
            return Verdict(
                False,
                [CheckResult(f"schema:{step.step_id}", False,
                             f"unknown tool {step.tool!r}")],
            )

        checks: list[CheckResult] = []

        # ---- layer 1: schema -------------------------------------------------
        if "schema" in step.verify:
            expected_kind = tool.output_contract.get("kind")
            for aid in result.outputs:
                try:
                    art = state.artifact(aid)
                except KeyError as exc:
                    checks.append(
                        CheckResult(f"schema:{aid}", False, str(exc))
                    )
                    continue
                checks.append(check_artifact(art, state.run_dir, expected_kind))

        if any(not c.passed for c in checks):
            return Verdict(False, checks)

        # ---- layer 2: semantic -------------------------------------------------
        if "semantic" in step.verify:
            inputs = self._resolved_inputs(step, tool, state)
            for aid in result.outputs:
                art = state.artifact(aid)
                checks.extend(
                    run_rules(tool.semantic_rules, art, inputs, state.run_dir)
                )

        from agent.verification.task import check_contract
        for contract in self.task_contracts:
            if step.output_name == contract["artifact"] and not any(
                state.artifact(aid).name == contract["artifact"] for aid in result.outputs
            ):
                checks.append(CheckResult("task:missing_output", False, "required task artifact was not produced"))
        for aid in result.outputs:
            art = state.artifact(aid)
            for contract in self.task_contracts:
                if contract["artifact"] == art.name:
                    checks.append(check_contract(contract, art, state.run_dir))
        return Verdict(all(c.passed for c in checks), checks)

    # ------------------------------------------------------------------ #
    def _resolved_inputs(self, step: Step, tool, state: "StateManager") -> list[Any]:
        inputs = []
        for key in tool.artifact_args:
            value = step.args.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                inputs.extend(state.lookup(v) for v in value)
            else:
                inputs.append(state.lookup(value))
        return inputs
