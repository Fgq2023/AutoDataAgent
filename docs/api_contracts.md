# API contracts between the three subtopic modules

> These contracts are the team's "constitution". `agent/core/types.py` is the
> single source of truth; this document is its human-readable mirror.
> Changes require review by **all three** owners (`.github/CODEOWNERS`).

## 1. Shared vocabulary — `agent/core/types.py`

```python
class StepStatus(str, Enum):
    PENDING, RUNNING, SUCCEEDED, FAILED, SKIPPED, RECOVERED

class RecoveryAction(str, Enum):
    RETRY, REPLAN, ROLLBACK, SKIP, FAIL

@dataclass
class Step:                 # produced by A, consumed by C/B
    step_id: str
    description: str
    tool: str                # key into the tool registry
    args: dict               # artifact args reference *names*, resolved by C
    depends_on: list[str]
    verify: list[str]        # ["schema"] or ["schema", "semantic"]
    output_name: str         # logical artifact name produced

@dataclass
class TaskGraph:
    goal: str
    steps: list[Step]
    # scheduling state lives on the graph: next_ready() / mark() / done()
    # cascade_skip(failed_id) → SKIPPED for all transitive dependents

@dataclass
class Artifact:             # owned by B
    artifact_id: str         # unique; versioned on re-runs (raw, raw_v2, …)
    name: str                # logical name referenced in step args
    kind: str                # dataframe | table | figure | text
    path: str                # relative to the run directory
    meta: dict               # {columns: [...], n_rows: int, ...}
    valid: bool              # False once invalidated (rollback / verify-fail)
    created_at: str

@dataclass
class StepResult:            # produced by C
    step_id: str
    status: StepStatus
    outputs: list[str]       # artifact ids
    error: str | None
    duration_ms: int
    attempts: int
    classification: str      # timeout | dependency | execution | verify
    verdict: Verdict | None

@dataclass
class Verdict:
    ok: bool
    checks: list[CheckResult]   # {name, passed, detail}

@dataclass
class ProvenanceRecord:
    artifact_id: str
    step_id: str
    tool: str
    input_artifact_ids: list[str]
    created_at: str

@dataclass
class RunContext:            # handed to every tool by the executor
    run_dir: str
    goal: str
    lookup(name) -> Artifact
    step_summaries() -> list[dict]
    provenance_lines(name) -> list[str]
    timeout_s: float
```

## 2. Subtopic A — planner interface (`agent/planning/base.py`)

```python
class BasePlanner(ABC):
    label: str                       # "mock" | "llm" (shown in traces)
    def plan(self, goal: str, ctx: DataContext) -> TaskGraph: ...
```

`DataContext` (built by `agent/core/context.py`) provides grounded profiles
(schema, row counts, quality issues) of every data source.

`Replanner.replan(goal, graph, failed_step, state) -> TaskGraph | None`
returns a graph with the failed step rescued (v0.1: tool substitution), or
`None` when no alternative exists (→ orchestrator FAIL path).

**A's obligations:** plans only use registered tools; artifact references
must be produced by steps in `depends_on`; graphs are acyclic. The LLM
planner validates all of this before emitting a graph.

## 3. Subtopic B — state manager interface (`agent/state/manager.py`)

```python
StateManager.new_run(outputs_root, goal, graph) -> StateManager

# step progress
mark(step_id, status, error=None, attempts=None, duration_ms=None)
checkpoint(step, result, verdict, final_status)
rollback(to_step_id) -> list[str]          # affected (downstream) steps

# artifacts & provenance
register_artifacts(step, specs, input_artifact_ids) -> list[Artifact]
invalidate_step_artifacts(step_id) -> list[str]
lookup(name) -> Artifact                   # latest VALID artifact by name
provenance_lines(name) -> list[str]

# recovery
decide_recovery(step_id, classification, attempts) -> RecoveryAction

# reporting surfaces
step_summaries() -> list[dict]
artifacts_summary() -> list[dict]
export_trace() -> Path                     # trace.json
```

**B's obligations:** `lookup` never returns invalidated artifacts; ids are
versioned so re-runs never shadow history; every recovery decision is
appended to the trace.

## 4. Subtopic C — tool & verification interfaces

```python
# agent/tools/base.py
class Tool(ABC):
    name: str
    description: str
    input_contract: dict[str, str]     # documentation for humans + the LLM
    output_contract: dict[str, str]    # {"kind": ...}
    artifact_args: list[str]           # arg keys that hold artifact NAMES
    semantic_rules: list[dict]         # [{"name": ..., "input_arg": ...}]

    def run(self, args, ctx: RunContext) -> list[ArtifactSpec]:
        # artifact args arrive resolved to Artifact objects (or lists)

# agent/verification/verifier.py
class Verifier:
    def verify(self, step, result, state) -> Verdict
    # layer 1: schema checks on every output artifact
    # layer 2: the tool's declared semantic rules (skipped if layer 1 fails)
```

**C's obligations:** tools never touch `agent.state` internals (only
`RunContext`); generated code runs through the sandbox
(`agent/tools/sandbox.py`), never in-process; every output is declared via
`ArtifactSpec` so B can register provenance.

## 5. Failure injection (demos & tests)

```python
FailureInjector("s3")            # transient: fail attempt 1 of step s3 (timeout)
FailureInjector("s3:permanent")  # permanent: fail every attempt of s3's
                                # ORIGINAL tool; a replanned tool is spared
Executor.check(...) raises ToolExecutionError(message, classification)
```

## 6. Extension points for the next phase

| subtopic | v0.1 | next |
|---|---|---|
| A | template planner; tool-substitution replan | LLM planning quality; graph regeneration with failure context |
| B | per-run state; policy-table recovery | cross-run memory; smarter rollback (artifact-level, not step-level) |
| C | subprocess sandbox; 2-layer verification | container isolation; cross-validation (second-method agreement) |
