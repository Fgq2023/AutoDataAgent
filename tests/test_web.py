import io
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
from agent.core.config import AgentConfig
from tests.conftest import TREND_GOAL


def test_http_run_artifacts_and_export(data_root, outputs_root):
    from agent.web.app import create_app
    app = create_app(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root)))
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/sources").json()["sources"]
        response = client.post("/api/runs", json={
            "spec": {"goal": TREND_GOAL, "sources": ["sales.csv"]},
            "mode": "mock", "sandbox_backend": "subprocess",
        })
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        app.state.service.wait(job_id)
        job = client.get(f"/api/runs/{job_id}").json()
        assert job["status"] == "finished"
        art = next(a for a in job["artifacts"] if a["name"] == "sales_monthly")
        assert client.get(f"/api/runs/{job_id}/artifacts/{art['artifact_id']}/preview").json()["rows"]
        response = client.get(f"/api/runs/{job_id}/export")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert {"state.db", "trace.json", "job.json"} <= set(archive.namelist())
        assert client.get(f"/api/runs/{job_id}/artifacts/missing").status_code == 404
        assert client.post("/api/runs", json={"spec": {"goal": "x", "sources": ["../secret.csv"]}}).status_code == 400
        assert client.post("/api/runs", json={"spec": {"goal": "x"}}, headers={"Origin": "https://foreign.test"}).status_code == 403


def test_http_clarification_and_artifact_bounds(data_root, outputs_root):
    import sqlite3
    from agent.web.app import create_app

    app = create_app(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root)))
    with TestClient(app) as client:
        response = client.post("/api/runs", json={"spec": {
            "goal": TREND_GOAL, "sources": ["sales.csv"],
            "questions": {"negative": "Remove invalid negative prices?"},
        }, "mode": "mock", "sandbox_backend": "subprocess"})
        job_id = response.json()["job_id"]
        assert response.json()["status"] == "awaiting_clarification"
        response = client.post(f"/api/runs/{job_id}/clarify",
                               json={"answers": {"negative": "Remove invalid negative prices"}})
        assert response.status_code == 202
        app.state.service.wait(job_id)
        status = client.get(f"/api/runs/{job_id}").json()
        art = status["artifacts"][0]
        secret = outputs_root / "outside.txt"
        secret.write_text("private", encoding="utf-8")
        with sqlite3.connect(Path(status["run_dir"]) / "state.db") as db:
            db.execute("UPDATE artifacts SET path=? WHERE artifact_id=?",
                       (str(secret), art["artifact_id"]))
        assert client.get(f"/api/runs/{job_id}/artifacts/{art['artifact_id']}/preview").status_code == 400
        assert client.get("/api/runs", headers={"Host": "foreign.test"}).status_code == 400


def test_http_cursor_and_input_validation(data_root, outputs_root):
    from agent.web.app import create_app
    app = create_app(AgentConfig(data_root=str(data_root), outputs_root=str(outputs_root)))
    with TestClient(app) as client:
        assert client.get("/api/runs/unknown").status_code == 404
        assert client.post("/api/runs", json={"spec": {"goal": "x", "unknown": 1}}).status_code == 400
        assert client.post("/api/runs", json={"spec": {"goal": "x"}, "mode": "invalid"}).status_code == 422
