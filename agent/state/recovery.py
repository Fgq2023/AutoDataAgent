"""Error classification and recovery policy (subtopic B).

Failures are classified into a small taxonomy, and each class maps to an
ordered policy of :class:`~agent.core.types.RecoveryAction` values that the
state manager walks through as attempts accumulate:

  =============  ==================================
  classification  policy (attempt 1, 2, 3, ...)
  =============  ==================================
  timeout        RETRY, RETRY, REPLAN
  dependency     REPLAN
  execution      RETRY, REPLAN
  verify         RETRY, REPLAN
  =============  ==================================

When the policy list is exhausted the step fails for good and its
dependents are cascade-skipped by the orchestrator.
"""

from __future__ import annotations

from agent.core.types import RecoveryAction

_TIMEOUT_ERRORS = ("timeout", "timed out", "timeoutexpired")
_DEPENDENCY_ERRORS = ("importerror", "modulenotfounderror", "no such tool", "unknown tool")


def classify_message(message: str) -> str:
    """Classify an error message into the recovery taxonomy."""
    m = (message or "").lower()
    if any(k in m for k in _TIMEOUT_ERRORS):
        return "timeout"
    if any(k in m for k in _DEPENDENCY_ERRORS):
        return "dependency"
    return "execution"


def classify_exception(exc: BaseException) -> str:
    return classify_message(f"{type(exc).__name__}: {exc}")


# Ordered policies: index = attempts already made (1-based).
POLICIES: dict[str, list[RecoveryAction]] = {
    "timeout": [RecoveryAction.RETRY, RecoveryAction.RETRY, RecoveryAction.REPLAN],
    "dependency": [RecoveryAction.REPLAN],
    "execution": [RecoveryAction.RETRY, RecoveryAction.REPLAN],
    "verify": [RecoveryAction.RETRY, RecoveryAction.REPLAN],
}


def decide_recovery(classification: str, attempts: int) -> RecoveryAction:
    """Return the recovery action for the *attempts*-th failure of a step."""
    policy = POLICIES.get(classification, POLICIES["execution"])
    if attempts <= 0:
        attempts = 1
    if attempts > len(policy):
        return RecoveryAction.FAIL
    return policy[attempts - 1]
