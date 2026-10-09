"""Subtopic C — tool execution & verification.

Importing this package registers **every** tool in :data:`REGISTRY`,
including the report tool that lives in :mod:`agent.output` (it is a tool
like any other: planned, executed, verified and traced).

Note on layering: ``agent.output.report`` imports ``agent.tools.base``,
which only depends on ``agent.core.types`` — so this cross-import is
cycle-safe.
"""

from agent.tools.base import REGISTRY, ArtifactSpec, Tool, get_tool, register
from agent.tools import builtins  # noqa: F401  (registers builtin tools)
from agent.tools.executor import Executor, FailureInjector, ToolExecutionError
from agent.output import ReportTool  # noqa: F401  (registers the report tool)

__all__ = [
    "REGISTRY",
    "ArtifactSpec",
    "Tool",
    "get_tool",
    "register",
    "Executor",
    "FailureInjector",
    "ToolExecutionError",
    "ReportTool",
    "builtins",
]
