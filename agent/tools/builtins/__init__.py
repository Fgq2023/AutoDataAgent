"""Builtin data tools (subtopic C).

Importing this package registers every builtin tool in the shared
:data:`~agent.tools.base.REGISTRY`.
"""

from agent.tools.builtins import (  # noqa: F401  (import = registration)
    aggregate,
    chart,
    clean_data,
    load_data,
    python_exec,
    sql_query,
)

__all__ = ["aggregate", "chart", "clean_data", "load_data", "python_exec", "sql_query"]
