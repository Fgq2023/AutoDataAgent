"""Tool abstraction and registry (subtopic C).

A tool is the agent's unit of data capability.  Each tool declares:

  * ``input_contract``   — what its args mean (documentation for the LLM
                           planner and for humans)
  * ``output_contract``  — what kind of artifact it produces
  * ``artifact_args``    — which args reference upstream *artifact names*
                           (resolved by the executor before running)
  * ``semantic_rules``   — declared sanity checks the verifier runs on
                           its outputs (schema checks are automatic)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from agent.core.types import RunContext

if TYPE_CHECKING:  # pragma: no cover
    from agent.core.types import Artifact


@dataclass
class ArtifactSpec:
    """A tool's declaration of one artifact it just produced.

    ``path`` is relative to the run directory; the state manager assigns
    the final unique ``artifact_id``.
    """

    name: str
    kind: str                 # dataframe | table | figure | text
    path: str
    meta: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """Base class for every data tool."""

    #: registry key
    name: str = ""
    description: str = ""
    input_contract: dict[str, str] = {}
    output_contract: dict[str, str] = {}
    #: arg keys whose values are artifact names (str or list[str])
    artifact_args: list[str] = []
    #: declared semantic rules: [{"name": ..., "input_arg": ..., "params": {...}}]
    semantic_rules: list[dict[str, Any]] = []

    @abstractmethod
    def run(self, args: dict[str, Any], ctx: RunContext) -> list[ArtifactSpec]:
        """Execute the tool.  Artifact args arrive already resolved by the
        executor to :class:`~agent.core.types.Artifact` objects (single
        value or list, matching how the planner wrote them)."""


REGISTRY: dict[str, Tool] = {}


def register(tool_cls: type[Tool]) -> type[Tool]:
    """Class decorator: instantiate and register a Tool subclass."""
    tool = tool_cls()
    if not tool.name:
        raise ValueError(f"{tool_cls.__name__} must define a non-empty `name`")
    REGISTRY[tool.name] = tool
    return tool_cls


def get_tool(name: str) -> Tool:
    return REGISTRY[name]
