from pathlib import Path

from agent.core.config import AgentConfig
from agent.core.task_spec import TaskSpec
from tests.conftest import TREND_GOAL


def test_service_clarification_and_run(data_root, outputs_root):
    from agent.service import RunService

    with RunService(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))) as service:
        spec = TaskSpec(TREND_GOAL, sources=["sales.csv"],
                        questions={"negative": "Remove invalid negative prices?"})
        job = service.start(spec)
        assert job["status"] == "awaiting_clarification"
        job = service.clarify(job["job_id"], {"negative": "Remove invalid negative prices"})
        service.wait(job["job_id"])
        snapshot = service.status(job["job_id"])
        assert snapshot["status"] == "finished"
        assert snapshot["steps"] and snapshot["artifacts"]
        assert any(event["event"] == "verification" for event in service.events(job["job_id"])["events"])
        assert service.preview(job["job_id"], "sales_monthly")["rows"]
        assert service.export(job["job_id"]).startswith(b"PK")


def test_service_serializes_and_cancels(data_root, outputs_root, monkeypatch):
    import threading
    import pytest
    from agent.service import RunService, ServiceBusy
    from agent.core.orchestrator import Orchestrator

    entered = threading.Event()
    release = threading.Event()
    original = Orchestrator.run

    def delayed(self, goal, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, goal, **kwargs)

    monkeypatch.setattr(Orchestrator, "run", delayed)
    with RunService(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))) as service:
        first = service.start(TaskSpec(TREND_GOAL, sources=["sales.csv"]))
        assert entered.wait(5)
        with pytest.raises(ServiceBusy):
            service.start(TaskSpec(TREND_GOAL, sources=["sales.csv"]))
        service.cancel(first["job_id"])
        release.set()
        service.wait(first["job_id"])
        assert service.status(first["job_id"])["status"] == "cancelled"


def test_service_restart_preserves_task_for_resume(data_root, outputs_root):
    import json
    from agent.service import RunService

    config = AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))
    spec = TaskSpec(TREND_GOAL, sources=["sales.csv"])
    with RunService(config) as service:
        job = service.start(spec)
        service.wait(job["job_id"])
        # Emulate the durable job state left by an exited server.
        path = outputs_root / "jobs" / (job["job_id"] + ".json")
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["status"] = "running"
        path.write_text(json.dumps(saved), encoding="utf-8")
    with RunService(config) as service:
        assert service.status(job["job_id"])["status"] == "interrupted"
        before = len(service.events(job["job_id"])["events"])
        service.resume(job["job_id"])
        service.wait(job["job_id"])
        assert service.status(job["job_id"])["status"] == "finished"
        new = service.events(job["job_id"], after=before)["events"]
        assert any(e["event"] == "run_resumed" for e in new)
        assert not any(e["event"] == "step_attempt" for e in new)


def test_service_rejects_second_owner(data_root, outputs_root):
    import pytest
    from agent.service import RunService

    config = AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))
    with RunService(config):
        with pytest.raises(ValueError, match="already being resumed"):
            RunService(config)


def test_status_recovers_sqlite_hot_journal(data_root, outputs_root):
    import subprocess
    import sys
    from agent.service import RunService

    config = AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))
    with RunService(config) as service:
        job = service.start(TaskSpec(TREND_GOAL, sources=["sales.csv"]))
        service.wait(job["job_id"])
        run = service.status(job["job_id"])["run_dir"]
        script = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_size=1')
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE trace SET payload=? WHERE seq=1", ('x' * 500000,))
os._exit(17)
"""
        child = subprocess.run([sys.executable, "-c", script, str(Path(run) / "state.db")], timeout=10)
        assert child.returncode == 17
        assert service.status(job["job_id"])["status"] == "finished"
        assert service.events(job["job_id"])["events"][0]["event"] == "run_started"


def test_unverified_artifact_is_not_previewable(data_root, outputs_root):
    import sqlite3
    import pytest
    from agent.service import RunService

    with RunService(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))) as service:
        job = service.start(TaskSpec(TREND_GOAL, sources=["sales.csv"]))
        service.wait(job["job_id"])
        status = service.status(job["job_id"])
        with sqlite3.connect(Path(status["run_dir"]) / "state.db") as db:
            db.execute("UPDATE steps SET status='running' WHERE step_id='s3'")
        with pytest.raises(KeyError, match="artifact"):
            service.preview(job["job_id"], "sales_monthly")


def test_export_blocks_concurrent_resume(data_root, outputs_root, monkeypatch):
    import threading
    import zipfile
    from concurrent.futures import ThreadPoolExecutor
    from agent.service import RunService

    with RunService(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))) as service:
        job = service.start(TaskSpec(TREND_GOAL, sources=["sales.csv"]))
        service.wait(job["job_id"])
        service.jobs[job["job_id"]]["status"] = "interrupted"
        writing = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        launched = threading.Event()
        original = zipfile.ZipFile.write

        def slow_write(self, *args, **kwargs):
            writing.set()
            assert release.wait(5)
            return original(self, *args, **kwargs)

        def launch(*args, **kwargs):
            launched.set()

        def resume():
            attempted.set()
            service.resume(job["job_id"])

        monkeypatch.setattr(zipfile.ZipFile, "write", slow_write)
        monkeypatch.setattr(service, "_launch", launch)
        with ThreadPoolExecutor(2) as pool:
            export = pool.submit(service.export, job["job_id"])
            assert writing.wait(5)
            resumer = pool.submit(resume)
            assert attempted.wait(5)
            blocked = not launched.wait(.15)
            release.set()
            assert export.result(timeout=5).startswith(b"PK")
            resumer.result(timeout=5)
            assert blocked, "resume started while the export was still being written"
            assert launched.is_set()


def test_export_respects_external_run_lock(data_root, outputs_root):
    import pytest
    from agent.service import RunService
    from agent.state.lock import run_lock

    with RunService(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))) as service:
        job = service.start(TaskSpec(TREND_GOAL, sources=["sales.csv"]))
        service.wait(job["job_id"])
        run = service.status(job["job_id"])["run_dir"]
        with run_lock(run):
            with pytest.raises(ValueError, match="already being resumed"):
                service.export(job["job_id"])


def test_cancel_during_planning_does_not_send_retry(data_root, outputs_root, monkeypatch):
    import threading
    import urllib.error
    from agent.service import RunService

    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocked(request, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        raise urllib.error.URLError("disconnected after cancel")

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr("urllib.request.urlopen", blocked)
    monkeypatch.setattr("time.sleep", lambda delay: None)
    with RunService(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root))) as service:
        job = service.start(TaskSpec(TREND_GOAL, sources=["sales.csv"]), mode="llm")
        assert entered.wait(5)
        service.cancel(job["job_id"])
        release.set()
        service.wait(job["job_id"])
        assert service.status(job["job_id"])["status"] == "cancelled"
        assert len(calls) == 1
