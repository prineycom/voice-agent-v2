"""Minimal FastAPI gateway: static React assets and narrow room capabilities only."""

from __future__ import annotations

from contextlib import asynccontextmanager
import re

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent_runtime import AgentRuntime
from .livekit_runtime import (
    SessionAttemptExpiredError,
    SessionCapacityError,
    SessionRegistry,
)
from .slice6_config import Slice6Settings, app_origin_allowed


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Slice6Settings.from_environment()
    if not settings.web_dist.is_dir() or not (settings.web_dist / "index.html").is_file():
        raise RuntimeError("Slice 6 web build is missing; run (cd web && npm run build)")
    agent_runtime = AgentRuntime.startup()
    registry = SessionRegistry(settings, agent_runtime=agent_runtime)
    try:
        await registry.start()
        app.state.settings = settings
        app.state.registry = registry
        app.state.agent_runtime = agent_runtime
        yield
    finally:
        await registry.close()


app = FastAPI(
    title="Voice Agent v2 Slice 6 gateway",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    livekit_url = getattr(getattr(request.app.state, "settings", None), "livekit_public_url", "")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        f"connect-src 'self' {livekit_url}; media-src 'self' blob:; "
        "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def public_status_document(
    registry: SessionRegistry, settings: Slice6Settings
) -> dict[str, object]:
    """Compose voice readiness independently from the soft agent plane."""

    health = registry.operational_health()
    environment_status_owner = getattr(getattr(registry, "runner", None), "agent_environment_status", None)
    environment_status = (
        environment_status_owner() if callable(environment_status_owner) else None
    )
    admission_ready = (
        registry.accepting and health["overall_readiness"] == "ready"
    )
    return {
        "schema_version": "voice-agent.public-operational-status.v2",
        "available": admission_ready and registry.active_count == 0,
        "accepting": admission_ready,
        "session_limit": 1,
        "build_id": settings.build_id,
        "release_id": settings.release_id,
        "provider_mode": "local",
        "external_provider_supervised": False,
        "automatic_fallback": False,
        "avatar_host_contract": "voice-agent.avatar-host.v1",
        "selected_avatar_module": "mvp-eye-svg-v1",
        "agent_runtime": (
            registry.agent_runtime.status_document(environment_status)
            if environment_status is not None
            else registry.agent_runtime.status_document()
        ),
        "health": health,
    }


@app.get("/api/status")
async def status(request: Request) -> dict[str, object]:
    registry: SessionRegistry = request.app.state.registry
    settings: Slice6Settings = request.app.state.settings
    return public_status_document(registry, settings)


ATTEMPT_HEADER = "x-voice-session-attempt"
ATTEMPT_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _admission_identity(request: Request, settings: Slice6Settings) -> str:
    if not app_origin_allowed(request.headers.get("origin"), settings.app_public_url):
        raise HTTPException(status_code=403, detail="A same-origin private application request is required")
    identity = request.headers.get(ATTEMPT_HEADER)
    if identity is None or ATTEMPT_ID_PATTERN.fullmatch(identity) is None:
        raise HTTPException(status_code=400, detail="A valid tab-scoped session attempt is required")
    return identity


@app.post("/api/session")
async def create_session(request: Request):
    settings: Slice6Settings = request.app.state.settings
    attempt_identity = _admission_identity(request, settings)
    registry: SessionRegistry = request.app.state.registry
    try:
        capability = await registry.create(attempt_identity)
    except SessionAttemptExpiredError as error:
        raise HTTPException(status_code=410, detail="The session attempt expired; retry CONNECT") from error
    except SessionCapacityError as error:
        raise HTTPException(status_code=409, detail="The measured single-session path is in use") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="The local voice path is not ready") from error
    return JSONResponse(capability, headers={"Cache-Control": "no-store"})


@app.delete("/api/session", status_code=204)
async def end_session(request: Request) -> Response:
    settings: Slice6Settings = request.app.state.settings
    attempt_identity = _admission_identity(request, settings)
    registry: SessionRegistry = request.app.state.registry
    try:
        await registry.end(attempt_identity)
    except Exception as error:
        raise HTTPException(status_code=503, detail="The local voice path did not close") from error
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


class DeferredStaticFiles(StaticFiles):
    """Resolve the environment-selected dist directory after application startup."""

    async def get_response(self, path: str, scope):
        settings = scope["app"].state.settings
        self.directory = str(settings.web_dist)
        self.all_directories = [str(settings.web_dist)]
        return await super().get_response(path, scope)


app.mount("/", DeferredStaticFiles(directory=".", html=True, check_dir=False), name="web")
