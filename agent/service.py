"""Shared execution and a persistent, single-worker local job service."""
from __future__ import annotations

import csv
import hashlib
from collections import Counter
import io
import json
import os
import sqlite3
import threading
import tempfile
import uuid
import zipfile
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.core.task_spec import ClarificationRequired, TaskSpec
from agent.llm.client import LLMClient
from agent.planning.llm_planner import LLMPlanner
from agent.planning.mock_planner import MockPlanner
from agent.planning.replanner import FeedbackReplanner, Replanner
from agent.state.manager import StateManager
from agent.state.store import RunStore
from agent.state.journal import load
from agent.tools import REGISTRY


class ServiceBusy(ValueError):
    pass


def execute_run(config, goal=None, *, resume=None, cancelled=None, state_factory=StateManager.new_run):
    """The same construction path for CLI and background Web execution."""
    if resume:
        db = Path(resume) / "state.db"
        if not db.is_file():
            raise ValueError("run state database not found")
        store = RunStore(db)
        try:
            config = AgentConfig(**load(store)["config"])
        finally:
            store.close()
        if config.mode == "llm" and not LLMClient().is_live:
            raise ValueError("saved LLM run requires live model credentials to resume")
    planner = MockPlanner() if config.mode == "mock" else LLMPlanner()
    agent = Orchestrator(
        planner, Replanner() if config.mode == "mock" else FeedbackReplanner(planner.client),
        REGISTRY, state_factory, config,
    )
    if resume:
        return agent.resume(resume, cancelled=cancelled)
    return agent.run(goal, cancelled=cancelled)


class RunService:
    active_states = {"queued", "running", "cancelling"}

    def __init__(self, config):
        self.config = replace(config, data_root=str(Path(config.data_root).resolve()),
                              outputs_root=str(Path(config.outputs_root).resolve()))
        self.root = Path(self.config.outputs_root)
        self.job_root = self.root / "jobs"
        self.job_root.mkdir(parents=True, exist_ok=True)
        from agent.state.lock import run_lock
        self._guard = run_lock(self.root)
        self._guard.__enter__()
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="data-agent")
        self.jobs = {}
        self.futures = {}
        self.cancel_events = {}
        for path in self.job_root.glob("*.json"):
            job = json.loads(path.read_text(encoding="utf-8"))
            self.jobs[job["job_id"]] = job
            if job["status"] in self.active_states:
                job["status"] = "interrupted"
                job["error"] = "Server stopped before this task finished. Resume after checking its trace."
                self._save(job)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self.lock:
            for event in self.cancel_events.values():
                event.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self._guard.__exit__(None, None, None)

    def _save(self, job):
        path = self.job_root / (job["job_id"] + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _job(self, job_id):
        if job_id not in self.jobs:
            raise KeyError("task not found")
        return self.jobs[job_id]

    def _check_idle(self):
        if any(job["status"] in self.active_states for job in self.jobs.values()):
            raise ServiceBusy("Another task is active. Cancel it or wait for completion.")

    def sources(self):
        from agent.core.context import profile_data_sources
        root = Path(self.config.data_root)
        result = []
        for profile in profile_data_sources(root).profiles:
            path = Path(profile.path).resolve()
            if path.is_relative_to(root) and not path.is_relative_to(self.root):
                result.append({**asdict(profile), "path": path.relative_to(root).as_posix()})
        return result

    def _validate_sources(self, spec):
        available = {item["path"] for item in self.sources()}
        if not set(spec.sources) <= available:
            raise ValueError("select data sources from the configured workspace")

    def start(self, spec, *, mode=None, sandbox_backend=None, inject_failure=None):
        self._validate_sources(spec)
        config = replace(self.config, mode=mode or self.config.mode,
                         sandbox_backend=sandbox_backend or self.config.sandbox_backend,
                         inject_failure=inject_failure)
        if config.mode not in ("mock", "llm"):
            raise ValueError("unknown model mode")
        with self.lock:
            self._check_idle()
            job = {"job_id": uuid.uuid4().hex, "status": "awaiting_clarification",
                   "spec": spec.to_dict(), "config": asdict(config), "run_dir": None,
                   "summary": None, "error": None, "questions": {}}
            self.jobs[job["job_id"]] = job
            try:
                spec.require_answers()
            except ClarificationRequired as exc:
                job["questions"] = exc.questions
                self._save(job)
            else:
                self._launch(job)
            return dict(job)

    def _launch(self, job, resume=False):
        job.update(status="queued", error=None, questions={})
        event = threading.Event()
        self.cancel_events[job["job_id"]] = event
        self._save(job)
        self.futures[job["job_id"]] = self.pool.submit(self._worker, job["job_id"], resume)

    def _worker(self, job_id, resume):
        with self.lock:
            job = self._job(job_id)
            event = self.cancel_events[job_id]
            if event.is_set():
                job["status"] = "cancelled"
                self._save(job)
                return
            job["status"] = "running"
            self._save(job)

        def factory(*args):
            state = StateManager.new_run(*args)
            with self.lock:
                job["run_dir"] = str(state.run_dir.resolve())
                self._save(job)
            return state

        try:
            summary = execute_run(
                AgentConfig(**job["config"]), TaskSpec(**job["spec"]),
                resume=job["run_dir"] if resume else None,
                cancelled=event.is_set, state_factory=factory,
            )
            with self.lock:
                job.update(summary=summary.to_dict(),
                           status="cancelled" if event.is_set() else summary.status)
        except ClarificationRequired as exc:
            with self.lock:
                job.update(status="awaiting_clarification", questions=exc.questions)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            key = os.environ.get("OPENAI_API_KEY")
            if key:
                message = message.replace(key, "[REDACTED]")
            with self.lock:
                job.update(status="cancelled" if event.is_set() else "failed", error=message)
        finally:
            with self.lock:
                self._save(job)

    def clarify(self, job_id, answers, sources=None):
        with self.lock:
            job = self._job(job_id)
            if job["status"] != "awaiting_clarification":
                raise ValueError("task is not waiting for clarification")
            self._check_idle()
            data = {**job["spec"], "answers": {**job["spec"]["answers"], **answers}}
            if sources is not None:
                data["sources"] = sources
            spec = TaskSpec(**data)
            spec.require_answers()
            self._validate_sources(spec)
            if "sources" in job["questions"] and not spec.sources:
                raise ValueError("select the required source files")
            job["spec"] = spec.to_dict()
            self._launch(job)
            return dict(job)

    def cancel(self, job_id):
        with self.lock:
            job = self._job(job_id)
            if job["status"] in self.active_states:
                self.cancel_events[job_id].set()
                job["status"] = "cancelling"
                self._save(job)
            elif job["status"] == "awaiting_clarification":
                job["status"] = "cancelled"
                self._save(job)
            return dict(job)

    def resume(self, job_id):
        with self.lock:
            self._check_idle()
            job = self._job(job_id)
            if job["status"] not in {"interrupted", "cancelled", "failed", "finished_with_failures"}:
                raise ValueError("task is not eligible for resume")
            run = self._run_path(job)
            self._validate_sources(TaskSpec(**job["spec"]))
            job["run_dir"] = str(run)
            self._launch(job, resume=True)
            return dict(job)

    def wait(self, job_id, timeout=60):
        self.futures[job_id].result(timeout=timeout)

    def _run_path(self, job):
        if not job["run_dir"]:
            raise ValueError("task has no resumable execution yet")
        path = Path(job["run_dir"]).resolve()
        if not path.is_relative_to(self.root) or not (path / "state.db").is_file():
            raise ValueError("run is outside this service workspace or is missing")
        return path

    def _read(self, job, query, args=()):
        if not job["run_dir"]:
            return []
        run = self._run_path(job)
        with closing(sqlite3.connect((run / "state.db").as_uri() + "?mode=rw", uri=True)) as db:
            db.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in db.execute(query, args)]
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return []
                raise

    def status(self, job_id):
        with self.lock:
            job = json.loads(json.dumps(self._job(job_id)))
        job["steps"] = self._read(job, "SELECT * FROM steps ORDER BY rowid")
        attempts = Counter(json.loads(row["payload"])["step_id"] for row in
                           self._read(job, "SELECT payload FROM trace WHERE event='step_attempt'"))
        for step in job["steps"]:
            step["attempts"] = max(step["attempts"], attempts[step["step_id"]])
        job["artifacts"] = self._read(job,
            "SELECT a.*, s.status AS producer_status FROM artifacts a "
            "JOIN provenance p ON a.artifact_id=p.artifact_id "
            "JOIN steps s ON p.step_id=s.step_id ORDER BY a.rowid")
        for art in job["artifacts"]:
            art["meta"] = json.loads(art["meta"])
            art["verified"] = bool(art["valid"] and art["producer_status"] in ("succeeded", "recovered"))
        manifests = self._read(job, "SELECT payload FROM resume_manifest WHERE id=1")
        manifest = json.loads(manifests[0]["payload"]) if manifests else {}
        job["plan"] = manifest.get("plan", {})
        job["budget"] = {key: manifest.get(key, 0) for key in ("elapsed", "model_requests")}
        return job

    def list_jobs(self):
        with self.lock:
            return [{"job_id": job["job_id"], "status": job["status"], "goal": job["spec"]["goal"]}
                    for job in reversed(list(self.jobs.values()))]

    def events(self, job_id, after=0):
        with self.lock:
            job = dict(self._job(job_id))
        rows = self._read(job, "SELECT * FROM trace WHERE seq>? ORDER BY seq", (after,))
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return {"events": rows, "cursor": rows[-1]["seq"] if rows else after}

    def artifact(self, job_id, artifact_id):
        with self.lock:
            job = dict(self._job(job_id))
        rows = self._read(job,
            "SELECT a.* FROM artifacts a JOIN provenance p ON a.artifact_id=p.artifact_id "
            "JOIN steps s ON p.step_id=s.step_id WHERE a.artifact_id=? AND a.valid=1 "
            "AND s.status IN ('succeeded','recovered')", (artifact_id,))
        if not rows:
            raise KeyError("valid artifact not found")
        art = rows[0]
        run = self._run_path(job)
        path = (run / art["path"]).resolve()
        if not path.is_relative_to(run) or not path.is_file():
            raise ValueError("artifact path is outside its run or missing")
        expected = json.loads(art["meta"]).get("sha256")
        if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("artifact content has changed since verification")
        return art, path

    def preview(self, job_id, artifact_id):
        art, path = self.artifact(job_id, artifact_id)
        if art["kind"] in ("table", "dataframe"):
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.reader(stream)
                columns = next(reader, [])
                rows = []
                for index, row in enumerate(reader):
                    if index >= 100:
                        return {"kind": "table", "columns": columns, "rows": rows, "truncated": True}
                    rows.append(row)
            return {"kind": "table", "columns": columns, "rows": rows, "truncated": False}
        if art["kind"] == "text":
            with path.open(encoding="utf-8") as stream:
                content = stream.read(200001)
            return {"kind": "text", "content": content[:200000], "truncated": len(content) > 200000}
        if art["kind"] == "figure":
            return {"kind": "figure"}
        raise ValueError("unsupported preview kind")

    def export(self, job_id):
        from agent.state.lock import run_lock
        with self.lock:
            job = self._job(job_id)
            if job["status"] in self.active_states:
                raise ServiceBusy("wait for the task to stop before exporting evidence")
            with run_lock(self._run_path(job)):
                return self._export(job_id)

    def _export(self, job_id):
        with self.lock:
            job = json.loads(json.dumps(self._job(job_id)))
        if job["status"] in self.active_states:
            raise ServiceBusy("wait for the task to stop before exporting evidence")
        run = self._run_path(job)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("job.json", json.dumps(job, ensure_ascii=False, indent=2))
            # SQLite backup gives the evidence archive a consistent database snapshot.
            with tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / "state.db"
                with closing(sqlite3.connect((run / "state.db").as_uri() + "?mode=rw", uri=True)) as db:
                    with closing(sqlite3.connect(target)) as disk:
                        db.backup(disk)
                archive.write(target, "state.db")
            for path in sorted(run.rglob("*")):
                if path.is_file() and path.resolve().is_relative_to(run):
                    if path.name in {"state.db", "state.db-wal", "state.db-shm", "resume.lock"}:
                        continue
                    archive.write(path, path.relative_to(run).as_posix())
        return buffer.getvalue()
