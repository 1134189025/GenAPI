from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import accounts, ai, register, system, user_management
from api.support import resolve_web_asset, start_limited_account_watcher
from services.config import config


def create_app() -> FastAPI:
    app_version = config.app_version

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = Event()
        thread = start_limited_account_watcher(stop_event)
        config.cleanup_old_images()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=1)

    app = FastAPI(title="Genapi", version=app_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ai.create_router())
    app.include_router(accounts.create_router())
    app.include_router(register.create_router())
    app.include_router(system.create_router(app_version))
    app.include_router(user_management.create_router(app_version))
    if config.images_dir.exists():
        app.mount("/images", StaticFiles(directory=str(config.images_dir)), name="images")

    @app.api_route(
        "/v1",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def removed_openai_compatible_api_root():
        raise HTTPException(status_code=404, detail="Not Found")

    @app.api_route(
        "/v1/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def removed_openai_compatible_api(full_path: str):
        raise HTTPException(status_code=404, detail="Not Found")

    @app.api_route(
        "/api",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def missing_api_root():
        raise HTTPException(status_code=404, detail="Not Found")

    @app.api_route(
        "/api/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def missing_api(full_path: str):
        raise HTTPException(status_code=404, detail="Not Found")

    @app.api_route(
        "/auth/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def removed_legacy_auth_api(full_path: str):
        raise HTTPException(status_code=404, detail="Not Found")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_web(full_path: str):
        normalized_path = full_path.strip("/")
        if normalized_path.startswith(("api/", "v1/")):
            raise HTTPException(status_code=404, detail="Not Found")
        asset = resolve_web_asset(full_path)
        if asset is not None:
            return FileResponse(asset)
        if normalized_path.startswith("_next/"):
            raise HTTPException(status_code=404, detail="Not Found")
        fallback = resolve_web_asset("")
        if fallback is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(fallback)

    return app
