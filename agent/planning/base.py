"""Planner interface (subtopic A).

A planner turns a natural-language analytical goal, *grounded in the
actual data sources* (the :class:`~agent.core.context.DataContext`), into
an executable :class:`~agent.core.types.TaskGraph`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent.core.context import DataContext
from agent.core.types import TaskGraph


class BasePlanner(ABC):
    #: shown in traces / reports
    label: str = "planner"

    @abstractmethod
    def plan(self, goal: str, ctx: DataContext) -> TaskGraph:
        """Decompose *goal* into a task graph grounded in *ctx*."""
