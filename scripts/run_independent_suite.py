"""Explicit live evaluation of prepared tasks; defaults to dev, at most 3 tasks.

Without --provide-contracts, references stay exclusively on the grader side.
No mock fallback. Test execution requires --allow-test to avoid accidental tuning.
"""
import argparse
import hashlib
import json
import sys
import tempfile
import uuid
from urllib.error import HTTPError, URLError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.core.types import Artifact
from agent.llm.client import LLMClient
from agent.planning.llm_planner import LLMPlanner
from agent.planning.replanner import FeedbackReplanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY
from agent.verification.task import check_contract


class StrictPlanner(LLMPlanner):
    label = "independent-live-strict"

    def plan(self, goal, ctx):
        graph = self._ask(goal, ctx)
        self._validate(graph)
        return graph


class RequestBudgetExceeded(RuntimeError):
    pass


class CountedClient(LLMClient):
    def __init__(self, batch_limit):
        super().__init__()
        self.calls = []
        self.batch_limit = batch_limit
        self.total = 0
        self.provider_stop = None

    def complete(self, *args, **kwargs):
        if self.total >= self.batch_limit or len(self.calls) >= 8:
            raise RequestBudgetExceeded("model request budget exhausted")
        if self.provider_stop:
            raise RuntimeError(self.provider_stop)
        self.total += 1
        try:
            return super().complete(*args, **kwargs)
        except Exception as exc:
            cause = exc.__cause__
            if isinstance(cause, HTTPError) and cause.code in (401, 402, 403, 429):
                self.provider_stop = "model_authentication" if cause.code in (401, 403) else "model_quota_or_rate_limit"
            raise
        finally:
            self.calls.append(self.last_call)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "validation", "test"], default="dev")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--offset", type=int, default=0, help="zero-based attempt index")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--batch-requests", type=int, default=24)
    parser.add_argument("--allow-test", action="store_true")
    parser.add_argument("--provide-contracts", action="store_true", help="contract-assisted regime, NOT NL-only evaluation")
    args = parser.parse_args()
    if args.limit < 1 or (args.split == "test" and not args.allow_test):
        parser.error("positive limit required; heldout test also requires --allow-test")
    if args.offset < 0 or args.repeats < 1 or not 1 <= args.batch_requests <= 24:
        parser.error("require nonnegative offset, positive repeats and 1–24 batch requests")
    client = CountedClient(args.batch_requests)
    if not client.is_live:
        parser.error("configure OPENAI_* process environment; this script makes real model calls")
    base = ROOT / "outputs/independent_v1"
    tasks = [json.loads(line) for line in (base / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    tasks = [t for t in tasks if t["split"] == args.split][:args.limit]
    provenance = json.loads((base / "provenance.json").read_text(encoding="utf-8"))
    for task in tasks:
        folder = base / task["data_root"]
        source = folder / (task["table"] + ".csv" if task["source_type"] == "csv" else "data.db")
        expected = provenance["prepared"][task["table"]]["csv_sha256"] if task["source_type"] == "csv" else provenance["sqlite_sha256"][task["domain"]]
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("prepared source changed; rerun preparation and audit before evaluation")
    output = Path(tempfile.mkdtemp(prefix="independent_eval_", dir=ROOT / "outputs"))
    code_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "agent").rglob("*.py"))}
    results = []
    attempts = [(task, repeat) for repeat in range(1, args.repeats + 1) for task in tasks]
    if args.offset >= len(attempts):
        parser.error("offset outside selected attempts")
    for index, (task, repeat) in enumerate(attempts[args.offset:], start=args.offset):
        if client.total >= client.batch_limit:
            break
        client.calls = []
        client.session_id = uuid.uuid4().hex
        contracts = json.loads((base / "grader_only" / (task["task_id"] + ".contract.json")).read_text(encoding="utf-8"))
        config = AgentConfig(data_root=str(base / task["data_root"]), outputs_root=str(output / f"{task['task_id']}_r{repeat}"), mode="llm", task_contracts=contracts if args.provide_contracts else [])
        item = {"task_id": task["task_id"], "repeat": repeat, "attempt_index": index, "answer_correct": False}
        try:
            summary = Orchestrator(StrictPlanner(client), FeedbackReplanner(client), REGISTRY, StateManager.new_run, config).run(task["question"])
            item["summary"] = summary.to_dict()
            if summary.status != "finished":
                item["failure_stage"] = "execution_or_verification"
            else:
                art = next((a for a in reversed(summary.artifacts) if a["name"] == task["artifact"] and a["valid"]), None)
                if art is None:
                    raise ValueError("required final answer artifact missing")
                verdict = check_contract(contracts[0], Artifact(art["artifact_id"], art["name"], art["kind"], art["path"]), summary.run_dir)
                item["answer_correct"] = verdict.passed and bool(summary.report_path)
                item["grade"] = {"passed": verdict.passed, "detail": verdict.detail}
                if not item["answer_correct"]:
                    item["failure_stage"] = "answer_or_report"
        except RequestBudgetExceeded as exc:
            item["failure_stage"] = "request_budget"
            item["error"] = str(exc)
        except KeyboardInterrupt:
            item["failure_stage"] = "cancelled"
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}".replace(client.api_key, "[REDACTED]")
            item["failure_stage"] = "model_transport" if (isinstance(exc, (TimeoutError, ConnectionError)) or isinstance(exc.__cause__, URLError) and not isinstance(exc.__cause__, HTTPError)) or "LLM unreachable" in str(exc) else "planning_or_runtime"
        if client.provider_stop:
            item["failure_stage"] = client.provider_stop
        item["model_calls"] = len(client.calls)
        item["usage"] = client.calls
        results.append(item)
        payload = {"split": args.split, "regime": "contract_assisted" if args.provide_contracts else "NL_only", "model": client.model,
                   "task_manifest_sha256": hashlib.sha256((base / "tasks.jsonl").read_bytes()).hexdigest(), "source_provenance": provenance, "results": results}
        if not item["answer_correct"] and client.total >= client.batch_limit:
            item["batch_budget_exhausted"] = True
        payload["next_offset"] = index + 1 if index + 1 < len(attempts) else None
        if client.provider_stop or item.get("failure_stage") == "cancelled":
            payload["stop_reason"] = item["failure_stage"]
        elif client.total >= client.batch_limit and payload["next_offset"] is not None:
            payload["stop_reason"] = "batch_request_limit"
        payload["batch_request_limit"] = client.batch_limit
        payload["batch_requests_used"] = client.total
        payload["source_sha256"] = code_hashes
        payload["metrics"] = {"tasks": len(results), "correct": sum(r["answer_correct"] for r in results),
                              "wrong_accepts": sum(r.get("summary", {}).get("status") == "finished" and not r["answer_correct"] for r in results),
                              "model_transport_failures": sum(r.get("failure_stage") == "model_transport" for r in results),
                              "model_calls": sum(r["model_calls"] for r in results)}
        (output / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# Independent suite evaluation", "", f"Regime: {payload['regime']}; split: {args.split}; model: {client.model}",
                 "", "| Task | Repeat | Correct | Failure stage | Calls |", "|---|---:|---|---|---:|"]
        lines += [f"| {r['task_id']} | {r['repeat']} | {r['answer_correct']} | {r.get('failure_stage', '-')} | {r['model_calls']} |" for r in results]
        lines += ["", json.dumps(payload["metrics"]), "", "Development-set observations; not a heldout/generalization estimate."]
        (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(task["task_id"], repeat, item["answer_correct"], flush=True)
        if client.provider_stop or item.get("failure_stage") == "cancelled":
            break
    print(output)


if __name__ == "__main__":
    main()
