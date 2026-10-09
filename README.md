# AutoDataAgent

**An autonomous AI agent system for complex data tasks** — HKUST-GZ DSAA4591
Final Year Project (2026–2027).

Given a user's high-level analytical goal, the agent **decomposes the task,
inspects heterogeneous data sources, plans a multi-step workflow, calls
appropriate tools, verifies intermediate results, and recovers from failures**
— producing tables, visualizations, and an analytical report with full
execution traces and data provenance.

Unlike chatbot-style data analysis, this project focuses on a **reliable
agentic workflow**: task state, tool execution traces, data lineage, error
detection, and recovery from failed intermediate steps.

---

## Quick start

```bash
git clone https://github.com/Fgq2023/AutoDataAgent.git
cd AutoDataAgent
pip install -e ".[dev]"

# generate the demo datasets (deterministic, seeded)
python scripts/make_demo_data.py
```

### Scenario 1 — trend analysis (the headline demo)

```bash
python -m agent "分析 data/sales.csv 的月度销售趋势，输出月度汇总表和趋势图"
```

```
AutoDataAgent run finished: finished
  steps    : 5 (ok 5, recovered 0, failed 0, skipped 0)
  run dir  : outputs/run_20261009_...
  report   : outputs/run_.../report.md
  trace    : outputs/run_.../trace.json
```

Every run produces, inside `outputs/run_<id>/`:

| file | meaning |
|---|---|
| `report.md` | the analytical report (summary, provenance, tables, figures) |
| `artifacts/*.csv` | intermediate dataframes (raw → clean → monthly) |
| `artifacts/*.png` | visualizations |
| `trace.json` | **full execution trace**: every attempt, verification verdict, recovery decision |
| `state.db` | SQLite: step progress, versioned artifacts, **provenance lineage**, event log |

### Reliability demos — failure injection

```bash
# transient failure at s3 -> classified timeout -> RETRY rescues
python -m agent "分析 data/sales.csv 的月度销售趋势" --inject-failure s3

# permanent failure at s3 -> RETRY exhausted -> REPLAN swaps
# aggregate -> aggregate_pivot (identical contract) -> run still finishes
python -m agent "分析 data/sales.csv 的月度销售趋势" --inject-failure s3:permanent
```

In the second demo the trace shows the full recovery arc:

```
step_attempt        {"step_id": "s3", "tool": "aggregate", "attempt": 1}
failure_injected    {"step_id": "s3", "attempt": 1}
recovery_decision   {"action": "retry"}
step_attempt        {"step_id": "s3", "tool": "aggregate", "attempt": 2}
recovery_decision   {"action": "replan"}
replan              {"from_tool": "aggregate", "to_tool": "aggregate_pivot"}
step_attempt        {"step_id": "s3", "tool": "aggregate_pivot", "attempt": 1}
step_finished       {"step_id": "s3", "status": "recovered"}
```

### Scenarios 2 & 3 — cleaning and SQL QA

```bash
python examples/demo_cleaning.py   # 数据清洗：缺失值/重复行/负值，前后对比报告
python examples/demo_sql.py        # SQL 问答：2024 年每个客户的总消费
```

### LLM planner (optional)

The system runs **fully offline** by default (deterministic mock planner —
same interface, same pipeline, CI-safe). With an API key the LLM planner
takes over, with strict validation and graceful fallback to mock on any
planning error:

```bash
cp .env.example .env   # set OPENAI_API_KEY (any OpenAI-compatible endpoint)
python -m agent "分析 data/sales.csv 的月度销售趋势" --mode llm
```

### Tests

```bash
pytest          # 41 tests: planning / state / tools / e2e — all offline
```

---

## Architecture — one system, three research subtopics

```
            natural-language goal
                     │
                     ▼
      ┌─────────────────────────────┐
      │  A · planning               │  decompose goal -> TaskGraph (DAG)
      │  agent/planning/            │  dynamic replanning on failure
      └──────────────┬──────────────┘
                     │  TaskGraph
                     ▼
      ┌─────────────────────────────┐
      │  B · state / memory         │  step progress, checkpoint/rollback
      │  agent/state/               │  artifact registry + provenance
      │                             │  recovery policy: RETRY/REPLAN/FAIL
      └──────────────┬──────────────┘
                     │  recovery decisions ──► A replans / C re-executes
                     ▼
      ┌─────────────────────────────┐
      │  C · tools & verification    │  registry + sandboxed execution
      │  agent/tools/ + verification/│  2-layer checks: schema + semantic
      └──────────────┬──────────────┘
                     ▼
          tables · figures · report.md
          trace.json · state.db (provenance)
```

| member | subtopic | owns | research questions |
|---|---|---|---|
| A | **Intelligent task decomposition & planning** | `agent/planning/` | goal → executable DAG; granularity; replanning strategies (LLM-driven) |
| B | **State management, memory & error recovery** | `agent/state/` | checkpoint/rollback; provenance lineage; failure taxonomy → recovery policies |
| C | **Tool execution & result verification** | `agent/tools/`, `agent/verification/` | capability contracts; sandbox isolation; schema/semantic/cross-validation |

`agent/core/` holds the **shared contracts** all three speak — changes there
are cross-team changes (see `.github/CODEOWNERS`).

### Data flow of a step

1. **plan** (A) emits `Step{tool, args, depends_on, verify}`
2. **execute** (C) resolves artifact references, runs the tool in isolation,
   registers artifacts + provenance with **state** (B)
3. **verify** (C) checks the *schema* layer, then the tool's declared
   *semantic* rules against input + output
4. **checkpoint** (B) persists the verdict; on failure the **recovery policy**
   decides RETRY / REPLAN / FAIL and the loop reacts

### Recovery policy (v0.1)

| failure class | policy (attempt 1, 2, 3, …) |
|---|---|
| `timeout` | RETRY, RETRY, REPLAN, … → FAIL |
| `execution` | RETRY, REPLAN, … → FAIL |
| `verify` | RETRY, REPLAN, … → FAIL |
| `dependency` | REPLAN, … → FAIL |

Failures cascade-skip dependents; recovered steps are marked `recovered`
and the report records every recovery event.

---

## Repository layout

```
agent/
  core/          shared contracts: types, data profiling, orchestrator, config
  planning/      A: mock planner, LLM planner, replanner
  state/         B: SQLite store, state manager, provenance, recovery policies
  tools/         C: registry, executor, sandbox + builtins (load/clean/aggregate/
                 aggregate_pivot/python_exec/sql_query/chart)
  verification/  C: schema checks + semantic rules
  output/        report assembly (itself a tool: planned/verified/traced)
  llm/           OpenAI-compatible client (stdlib only) + offline mock
data/            demo datasets (regenerate with scripts/make_demo_data.py)
examples/        runnable demos for the three syllabus scenarios
tests/           41 offline tests incl. e2e failure-injection scenarios
docs/            architecture + API contracts (the team's "constitution")
.github/         CODEOWNERS (subtopic ownership) + CI (pytest, offline)
```

## Team & academic integrity

This is a Final Year Project for **DSAA4591 (HKUST-GZ)**, 2026–2027.
Code ownership maps to research subtopics (`.github/CODEOWNERS`); each
member's thesis chapter documents their subtopic. External code or ideas
are cited in `docs/architecture.md`.

## License

MIT — see [LICENSE](LICENSE).
