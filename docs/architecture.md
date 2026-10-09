# AutoDataAgent — Architecture

> The authoritative architecture description. `docs/api_contracts.md` holds the
> interface contracts between the three subtopic modules.

## 1. Positioning

AutoDataAgent is an **autonomous data agent**: given a high-level analytical
goal it plans a multi-step workflow over heterogeneous data sources (CSV /
Excel / JSON / SQLite in v0.1), executes tools in isolation, **verifies every
intermediate result**, and **recovers from failed steps** — all while
recording task state, execution traces, and data provenance.

This is deliberately *not* a chatbot-with-a-code-interpreter: the reliability
machinery (state, provenance, verification, recovery) is the point.

## 2. The three research subtopics

One system, three owners. Directory ownership is enforced socially via
`.github/CODEOWNERS` (and later via branch protection).

| subtopic | owner | module | research focus |
|---|---|---|---|
| **A — Intelligent task decomposition & planning** | member A | `agent/planning/` | goal → executable DAG; granularity control; LLM planning with strict validation; dynamic replanning |
| **B — State management, memory & error recovery** | member B | `agent/state/` | checkpoint / rollback; artifact versioning; provenance lineage; failure taxonomy → recovery policy |
| **C — Tool execution & result verification** | member C | `agent/tools/`, `agent/verification/` | capability contracts; sandbox isolation; schema + semantic verification layers |

Shared: `agent/core/` (types, data profiling, orchestrator, config),
`agent/cli.py`, `agent/output/`, `agent/llm/`.

## 3. Control loop

```
profile data sources ──► plan (A) ──► for each ready step:
                                          execute (C, sandboxed)
                                            │
                                            ▼
                                          verify (C: schema → semantic)
                                            │ ok                     │ fail
                                            ▼                        ▼
                                     checkpoint (B)         classify failure
                                            │                        │
                                            ▼                        ▼
                                       next step          recovery policy (B)
                                                                     │
                                              RETRY ◄────────────────┤
                                              (next attempt)         │
                                              REPLAN ──► A swaps ────┤
                                              FAIL ──► cascade-skip ─┘
done ──► assemble report (tool) ──► export trace.json
```

Key invariants:

* every artifact is registered with a **provenance record** before it can
  be referenced downstream;
* every attempt gets a **verdict** (`trace.json: verification`);
* a step that only succeeded after retry/replan is marked `recovered`,
  never silently `succeeded`;
* artifacts of failed attempts are **invalidated** (kept for audit, excluded
  from lookup) and re-runs get versioned ids (`raw`, `raw_v2`, …).

## 4. Failure taxonomy & recovery policy (v0.1)

| class | detected from | policy |
|---|---|---|
| `timeout` | sandbox wall-clock budget exceeded | RETRY, RETRY, REPLAN → FAIL |
| `dependency` | unknown tool / import errors | REPLAN → FAIL |
| `execution` | tool raised | RETRY, REPLAN → FAIL |
| `verify` | schema or semantic check failed | RETRY, REPLAN → FAIL |

REPLAN (v0.1) = tool substitution with identical contract
(`aggregate` ↔ `aggregate_pivot`). LLM-driven regeneration of the downstream
graph is the main research direction for subtopic A in the next phase.

## 5. Verification layers (v0.1)

1. **Schema layer** (always on when `verify` includes `schema`): file exists
   and is non-empty; kind matches the tool's output contract; dataframes
   load back with exactly the recorded shape (row/column drift is caught).
2. **Semantic layer** (tool-declared rules): e.g. `no_row_growth` — a
   clean/aggregate step cannot output more rows than its input;
   `row_count_positive`; `non_negative`; `figure_nonempty`.

Cross-validation (recompute with a second method and compare) is planned for
v0.2 (subtopic C research direction).

## 6. Storage model

Each run owns a directory:

```
outputs/run_<id>/
  report.md            final analytical report
  trace.json           append-only event log (all decisions)
  state.db             SQLite: runs / steps / artifacts / provenance / trace
  artifacts/*.csv|png  intermediate products
```

`state.db` schema is defined in `agent/state/store.py`. Artifacts keep an
`valid` flag: rollback/invalidation flips it to 0 but rows are never
deleted — audit history is preserved.

## 7. Design decisions worth defending in the thesis

* **Mock-first planning.** A deterministic, offline planner behind the same
  interface as the LLM planner makes the whole system testable and CI-safe,
  and provides a baseline for evaluating LLM planning quality.
* **Provenance on registration, not on success.** Artifacts are registered
  when produced; verification failure *invalidates* them. This keeps failed
  attempts auditable without letting them flow downstream.
* **Recovery as policy, not vibes.** A declarative table
  (`agent/state/recovery.py`) maps failure class + attempt count → action,
  making recovery behaviour inspectable and comparable across experiments.
* **The report is a tool.** Report assembly runs through the same
  plan/execute/verify/trace pipeline as everything else.

## 8. Roadmap (mapped to FYP milestones)

| phase | focus | milestone |
|---|---|---|
| 2026 H2 | v0.1 (this): full loop, mock planner, recovery, provenance, 3 scenarios | **mid-term review (Dec 15, 2026)** |
| 2027 H1 | A: LLM planning quality + replanning research; B: rollback strategies, memory across runs; C: cross-validation, container isolation | final delivery |
| evaluation | benchmark on data cleaning / table QA / SQL analysis / report generation vs. baselines | thesis + defence (May 2027) |
