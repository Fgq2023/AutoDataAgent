"""Local single-process HTTP API over the shared RunService."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agent.core.config import AgentConfig
from agent.core.task_spec import TaskSpec
from agent.llm.client import LLMClient
from agent.service import RunService, ServiceBusy

STATIC = Path(__file__).parent / "static"


class StartRequest(BaseModel):
    spec: dict[str, Any]
    mode: Literal["mock", "llm"] = "mock"
    sandbox_backend: Literal["docker", "subprocess"] = "docker"
    inject_failure: Literal["s3", "s3:permanent", "s4:permanent"] | None = None


class AnswersRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    sources: list[str] | None = None


def create_app(config=None):
    config = config or AgentConfig(outputs_root="outputs/web", sandbox_backend="docker")

    @asynccontextmanager
    async def lifespan(app):
        service = RunService(config)
        app.state.service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="AutoDataAgent", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get("host") or parsed.scheme not in ("http", "https"):
                return JSONResponse({"detail": "Only the local application origin is allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.exception_handler(ServiceBusy)
    async def busy(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    def settings():
        client = LLMClient()
        return {"data_root": config.data_root, "mode": config.mode,
                "sandbox_backend": config.sandbox_backend,
                "live_configured": client.is_live, "model": client.model if client.is_live else None}

    @app.get("/api/sources")
    def sources():
        return {"sources": app.state.service.sources()}

    @app.get("/api/runs")
    def runs():
        return {"runs": app.state.service.list_jobs()}

    @app.post("/api/runs", status_code=202)
    def start(body: StartRequest):
        try:
            spec = TaskSpec(**body.spec)
        except TypeError as exc:
            raise ValueError("TaskSpec contains unknown or missing fields") from exc
        if body.mode == "llm" and body.inject_failure:
            raise ValueError("failure injection is available in explicit mock demonstrations")
        if body.mode == "llm" and not LLMClient().is_live:
            raise ValueError("Configure live model credentials in the server environment first")
        return app.state.service.start(spec, mode=body.mode, sandbox_backend=body.sandbox_backend,
                                       inject_failure=body.inject_failure)

    @app.get("/api/runs/{job_id}")
    def status(job_id: str):
        return app.state.service.status(job_id)

    @app.get("/api/runs/{job_id}/events")
    def events(job_id: str, after: int = 0):
        if after < 0:
            raise ValueError("event cursor must be nonnegative")
        return app.state.service.events(job_id, after)

    @app.post("/api/runs/{job_id}/clarify", status_code=202)
    def clarify(job_id: str, body: AnswersRequest):
        return app.state.service.clarify(job_id, body.answers, body.sources)

    @app.post("/api/runs/{job_id}/cancel")
    def cancel(job_id: str):
        return app.state.service.cancel(job_id)

    @app.post("/api/runs/{job_id}/resume", status_code=202)
    def resume(job_id: str):
        return app.state.service.resume(job_id)

    @app.get("/api/runs/{job_id}/artifacts/{artifact_id}/preview")
    def preview(job_id: str, artifact_id: str):
        return app.state.service.preview(job_id, artifact_id)

    @app.get("/api/runs/{job_id}/artifacts/{artifact_id}")
    def artifact(job_id: str, artifact_id: str):
        art, path = app.state.service.artifact(job_id, artifact_id)
        media = {"figure": "image/png", "text": "text/plain; charset=utf-8",
                 "dataframe": "text/csv; charset=utf-8", "table": "text/csv; charset=utf-8"}
        return FileResponse(path, media_type=media[art["kind"]], filename=path.name,
                            content_disposition_type="inline")

    @app.get("/api/runs/{job_id}/export")
    def export(job_id: str):
        return Response(app.state.service.export(job_id), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="run-{job_id}.zip"'})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
