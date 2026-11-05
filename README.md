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
takes over, with strict validation and explicit errors on any
planning error:

```bash
cp .env.example .env   # set OPENAI_API_KEY (any OpenAI-compatible endpoint)
python -m agent "分析 data/sales.csv 的月度销售趋势" --mode llm
```

### Tests

```bash
pytest          # 41 tests: planning / state / tools / e2e — all offline
```

### Reproduce the offline scenario baseline

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts/run_baseline.py
```

On Linux/macOS use `.venv/bin/python` instead. The baseline explicitly uses
the mock planner, even if API credentials are configured. It generates its
own seeded datasets without overwriting `data/`, and checks trend analysis,
cleaning, SQL, retry recovery, replan recovery and terminal failure handling.
Each invocation writes `outputs/baseline_*/report.md`, machine-readable
`results.json`, and per-scenario reports, charts, traces and state databases.
Answer checks use independent standard-library calculations, not the agent's
own verifier. A nonzero exit means at least one scenario failed its checks.
This is a regression baseline for existing demos, not an LLM capability benchmark.

### Feedback-driven local repair

LLM mode now feeds execution errors, verification verdicts and input artifact
schemas to a parameter repair model. It preserves the tool, graph dependencies,
artifact references and output name, and reuses successful upstream results.
Mock mode retains deterministic tool substitution. `--max-attempts` (default 3)
limits total executions per step across repairs; `--max-replans` (default 3)
limits recovery planning requests for the run. Neither budget resets on repair.

With a live model configured, compare both policies on a deliberately corrupted
chart parameter using `python scripts/run_repair_validation.py`. This tests one
injected failure, not general repair accuracy. See `docs/local-repair.md`.

For five fault types plus clean/missing-source controls, run
`python scripts/run_repair_benchmark.py --live` with model credentials configured.
Without `--live` it uses an explicit known-answer oracle for offline regression
checks, not a model. Results include independent answer checks, model call counts,
token usage when supplied by the provider, latency and upstream re-executions.
See `docs/repair-benchmark.md` for the measured results and limitations.

### Explicit task-level semantic checks

For the demo orders schema, an optional independent contract checks customer
totals against a specified source database and year, regardless of the SQL
generated by the planner. It detects executable queries with the wrong year
or AVG instead of SUM. Contract failures feed the same bounded repair loop.

```powershell
.\.venv\Scripts\python.exe -m agent "查询 orders.db 中 2024 年每个客户的总消费金额" --task-contracts docs/task-contracts.example.json
```

Contracts are supplied explicitly; arbitrary natural-language requirements are
not automatically verified. In LLM mode SQL queries can be repaired, with source
and output names preserved and read-only execution enforced. See
`docs/task-semantics.md` for scope and the real-model comparison.

Generic `tabular_aggregate` contracts now support CSV / a single SQLite table,
typed filters, multiple grouping keys (including month/year), and sum/count/mean.
They are passed to planning and independently evaluated from source records.
See `docs/generic-task-contracts.md` and `docs/tabular-contracts.example.json`.
`python scripts/run_generic_semantic_validation.py` verifies a real initial model
plan followed by controlled semantic faults and real-model repair.

The public-data task suite is documented in `benchmarks/independent_v1/README.md`.
Run `python scripts/prepare_independent_suite.py` to download attributed UCI data
and validate 30 reference answers. Live evaluation is explicit and defaults to
three dev tasks; the test split requires an additional flag. After task setup,
the project plan is tracked in `docs/objectives-roadmap-2026-11-02.md`.

Python tool parameters can now be repaired from execution/verification feedback,
with syntax validation and the existing attempt budgets. This does not establish
a security sandbox. Registered artifacts retain content-hash snapshots across
retries; the artifact registry's path is authoritative, while tool output paths
remain compatibility aliases.

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

### TaskSpec 与跨源任务

```powershell
.\.venv\Scripts\python.exe -m examples.demo_cross_source outputs/cross-source-demo
```

示例生成合成 CSV 和 SQLite，并用固定计划执行读取、查询、多对一连接、聚合和报告。区域 East 的总额为 85，West 为 70。负数退款保留。

`task.json` 中的 `questions` 与 `answers` 使用相同的键。未填写答案时，CLI 会列出待确认问题并退出。填写答案后，可用配置了模型的 CLI 执行同一份规格。

```powershell
.\.venv\Scripts\python.exe -m agent --mode llm --task-spec outputs/cross-source-demo/task.json --data-root outputs/cross-source-demo
```

`sources` 是相对于数据根目录的文件路径。 `output_columns` 按产物名称声明实际输出列及其顺序。当前确认流程使用调用方显式列出的关键问题。

### M3 恢复与隔离执行

新运行支持用 --resume 指向运行目录，恢复会校验输入、计划清单和已验证产物，继续累计预算。LLM 模式恢复需要模型凭据，端点、模型名和会话沿用保存的运行。

Docker 后端先构建 docker/Dockerfile.sandbox 为 autodataagent-sandbox:local，再通过 --sandbox-backend docker 选择。scripts/verify_m3.py 可显式执行真实 Docker 验收。独立任务评测默认要求 Docker。

详细复现命令、实测证据与适用范围见 docs/m3-status-2026-11-05.md。


### 本地 Web 工作台（M4）

安装可选依赖后运行：

    python -m pip install -e ".[web]"
    python -m agent.web --data-root data --outputs-root outputs/web --port 8000

打开 http://127.0.0.1:8000 。页面支持目标与数据选择、关键口径确认、启动/取消/恢复、步骤和验证轨迹、表格/图表/报告预览，以及完整证据 ZIP 导出。CLI 与 Web 共用 execute_run 构造与执行入口。

运行 python scripts/rehearse_m4.py 可完成两轮正常、错误恢复和真实服务进程中断恢复演练。详细使用说明见 docs/m4-local-web.md，期中候选技术报告见 docs/midterm-report-2026-11-05.md。
