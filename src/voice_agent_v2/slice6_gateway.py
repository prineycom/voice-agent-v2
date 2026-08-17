"""Minimal FastAPI gateway: static React assets and narrow room capabilities only."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .livekit_runtime import SessionCapacityError, SessionRegistry
from .slice6_config import Slice6Settings, app_origin_allowed


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Slice6Settings.from_environment()
    if not settings.web_dist.is_dir() or not (settings.web_dist / "index.html").is_file():
        raise RuntimeError("Slice 6 web build is missing; run (cd web && npm run build)")
    registry = SessionRegistry(settings)
    try:
        await registry.start()
        app.state.settings = settings
        app.state.registry = registry
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


@app.get("/api/status")
async def status(request: Request) -> dict[str, object]:
    registry: SessionRegistry = request.app.state.registry
    settings: Slice6Settings = request.app.state.settings
    health = registry.operational_health()
    return {
        "schema_version": "voice-agent.public-operational-status.v1",
        "available": (
            registry.accepting and registry.active_count == 0
            and health["overall_readiness"] == "ready"
        ),
        "accepting": registry.accepting,
        "session_limit": 1,
        "build_id": settings.build_id,
        "release_id": settings.release_id,
        "provider_mode": "local",
        "external_provider_supervised": False,
        "automatic_fallback": False,
        "avatar_host_contract": "voice-agent.avatar-host.v1",
        "selected_avatar_module": "mvp-eye-svg-v1",
        "health": health,
    }


@app.post("/api/session")
async def create_session(request: Request):
    settings: Slice6Settings = request.app.state.settings
    if not app_origin_allowed(request.headers.get("origin"), settings.app_public_url):
        raise HTTPException(status_code=403, detail="A same-origin private application request is required")
    registry: SessionRegistry = request.app.state.registry
    try:
        capability = await registry.create()
    except SessionCapacityError as error:
        raise HTTPException(status_code=409, detail="The measured single-session path is in use") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="The local voice path is not ready") from error
    return JSONResponse(capability, headers={"Cache-Control": "no-store"})


class DeferredStaticFiles(StaticFiles):
    """Resolve the environment-selected dist directory after application startup."""

    async def get_response(self, path: str, scope):
        settings = scope["app"].state.settings
        self.directory = str(settings.web_dist)
        self.all_directories = [str(settings.web_dist)]
        return await super().get_response(path, scope)


app.mount("/", DeferredStaticFiles(directory=".", html=True, check_dir=False), name="web")
