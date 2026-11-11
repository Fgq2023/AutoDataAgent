"""Two complete M4 rehearsals against a real localhost HTTP server.

Uses synthetic demo data and explicit mock planning. Provider keys are removed
from the server environment. Each round verifies normal, recovered and resumed
execution, previews and evidence exports.
"""
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import make_demo_data
from scripts.run_baseline import check_answers

GOAL = "分析 sales.csv 的月度销售趋势，输出月度汇总表和趋势图"


def until(action, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = action()
        if predicate(last):
            return last
        time.sleep(0.02)
    raise AssertionError(f"deadline expired: {last}")


def request(client, method, path, payload=None):
    response = client.request(method, path, json=payload)
    response.raise_for_status()
    return response.json()


def main():
    (ROOT / "outputs").mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="m4_rehearsal_", dir=ROOT / "outputs"))
    results = []
    for round_number in (1, 2):
        folder = output / f"round_{round_number}"
        data = folder / "data"
        data.mkdir(parents=True)
        make_demo_data.DATA_DIR = data
        make_demo_data.make_sales_csv()
        make_demo_data.make_orders_db()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False)
        server = None
        log = (folder / "server.log").open("w", encoding="utf-8")
        environment = os.environ.copy()
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENCODE_API_KEY"):
            environment.pop(key, None)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        def launch():
            nonlocal server
            server = subprocess.Popen(
                [sys.executable, "-m", "agent.web", "--data-root", str(data),
                 "--outputs-root", str(folder / "web"), "--port", str(port)],
                cwd=ROOT, env=environment, stdout=log, stderr=log, creationflags=creationflags,
            )

            def ready():
                try:
                    return client.get("/api/config").status_code
                except httpx.TransportError:
                    return 0
            until(ready, lambda status: status == 200)

        def start(injection=None, questions=None):
            return request(client, "POST", "/api/runs", {
                "spec": {"goal": GOAL, "sources": ["sales.csv"], "questions": questions or {}},
                "mode": "mock", "sandbox_backend": "docker", "inject_failure": injection,
            })

        def snapshot(job):
            return request(client, "GET", "/api/runs/" + job["job_id"])

        def finish(job, kind, resumed=False):
            status = until(lambda: snapshot(job), lambda item: item["status"] not in
                           {"queued", "running", "cancelling"})
            assert status["status"] == "finished", status
            events = request(client, "GET", f"/api/runs/{job['job_id']}/events")["events"]
            attempts = Counter(event["payload"]["step_id"] for event in events
                               if event["event"] == "step_attempt")
            expected = sum(count > 1 for count in attempts.values()) if resumed else None
            checks = check_answers(kind, SimpleNamespace(**status["summary"]), data,
                                   expected_recovered=expected)
            assert any(event["event"] == "verification" for event in events)
            figures = [a for a in status["artifacts"] if a["kind"] == "figure" and a["valid"]]
            assert figures
            for art in status["artifacts"]:
                if not art["valid"]:
                    continue
                base = f"/api/runs/{job['job_id']}/artifacts/{art['artifact_id']}"
                preview = request(client, "GET", base + "/preview")
                if art["kind"] in ("dataframe", "table"):
                    assert preview["rows"]
                elif art["kind"] == "figure":
                    assert client.get(base).content.startswith(b"\x89PNG\r\n\x1a\n")
                elif art["kind"] == "text":
                    assert "## 验证与限制" in preview["content"]
            archive = client.get(f"/api/runs/{job['job_id']}/export")
            archive.raise_for_status()
            (folder / f"{kind}_{job['job_id']}.zip").write_bytes(archive.content)
            with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
                assert {"state.db", "trace.json", "job.json"} <= set(bundle.namelist())
                assert bundle.read("state.db").startswith(b"SQLite format 3")
            return status, checks, events

        try:
            launch()
            normal = start(questions={"negative": "Remove invalid negative price rows?"})
            assert normal["status"] == "awaiting_clarification"
            request(client, "POST", f"/api/runs/{normal['job_id']}/clarify",
                    {"answers": {"negative": "Remove invalid negative prices as specified by the demo"}})
            status, checks, events = finish(normal, "trend")
            results.append({"round": round_number, "case": "normal_with_clarification",
                            "passed": True, "job_id": normal["job_id"], "checks": checks})

            recovery = start("s3:permanent")
            status, checks, events = finish(recovery, "replan")
            assert status["summary"]["n_replans"] > 0
            results.append({"round": round_number, "case": "error_detection_and_recovery",
                            "passed": True, "job_id": recovery["job_id"], "checks": checks})

            interrupted = start()
            stopped = until(lambda: snapshot(interrupted), lambda item:
                            any(step["status"] in ("succeeded", "recovered") for step in item["steps"]))
            assert stopped["status"] == "running", "task completed before interruption; repeat rehearsal"
            reused = [step["step_id"] for step in stopped["steps"]
                      if step["status"] in ("succeeded", "recovered")]
            server.kill()
            server.wait(timeout=10)
            launch()
            assert snapshot(interrupted)["status"] == "interrupted"
            request(client, "POST", f"/api/runs/{interrupted['job_id']}/resume")
            status, checks, events = finish(interrupted, "trend", resumed=True)
            for step_id in reused:
                assert sum(event["event"] == "step_attempt" and event["payload"]["step_id"] == step_id
                           for event in events) == 1
            assert any(event["event"] == "run_resumed" for event in events)
            results.append({"round": round_number, "case": "server_process_interruption_and_resume",
                            "passed": True, "job_id": interrupted["job_id"], "reused_steps": reused,
                            "checks": checks})
        finally:
            if server and server.poll() is None:
                server.terminate()
                server.wait(timeout=15)
            log.close()
            client.close()
            payload = {
                "mode": "explicit mock; synthetic data; real HTTP server",
                "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sorted((ROOT / "agent").rglob("*")) if path.is_file()
                                 and "__pycache__" not in str(path)},
                "results": results,
            }
            (output / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"round {round_number}: normal, recovery, process resume PASS", flush=True)
    print(output)


if __name__ == "__main__":
    main()
