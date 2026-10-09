"""Provenance (data lineage) helpers (subtopic B).

The provenance table records, for every artifact, which step/tool produced
it and from which input artifacts.  These helpers walk that graph backwards
so any result can answer: "where did this number come from?".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.core.types import ProvenanceRecord

if TYPE_CHECKING:  # pragma: no cover
    from agent.state.store import RunStore


def lineage(store: "RunStore", artifact_id: str) -> list[ProvenanceRecord]:
    """Backward chain of provenance records, newest artifact first."""
    chain: list[ProvenanceRecord] = []
    seen: set[str] = set()
    queue = [artifact_id]
    while queue:
        aid = queue.pop(0)
        if aid in seen:
            continue
        seen.add(aid)
        rec = store.provenance(aid)
        if rec is None:
            continue
        chain.append(rec)
        queue.extend(rec.input_artifact_ids)
    return chain


def render_lineage(store: "RunStore", artifact_id: str) -> list[str]:
    """Human-readable lineage lines for *artifact_id*.

    Example::

        monthly_summary <- [s3] aggregate <- clean_sales
        clean_sales     <- [s2] clean_data <- raw_sales
        raw_sales       <- [s1] load_data <- (source: date, region, product, ...)
    """
    lines: list[str] = []
    for rec in lineage(store, artifact_id):
        art = store.artifact(rec.artifact_id)
        if rec.input_artifact_ids:
            inputs = ", ".join(rec.input_artifact_ids)
        elif art is not None and art.kind in ("dataframe", "table"):
            cols = ", ".join(
                c.get("name", "?") for c in art.meta.get("columns", [])[:8]
            )
            inputs = f"(source: {cols})"
        else:
            inputs = "(source)"
        lines.append(f"{rec.artifact_id} <- [{rec.step_id}] {rec.tool} <- {inputs}")
    return lines
