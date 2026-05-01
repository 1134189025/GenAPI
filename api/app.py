from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from threading import Event

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from api import accounts, ai, gallery, register, system, update, user_management
from api.support import resolve_web_asset, start_image_cache_watcher, start_limited_account_watcher
from services.config import config

STALE_NEXT_SCRIPT = (
    "try{var k='genapi:stale-next-script-reload';"
    "if(!sessionStorage.getItem(k)){sessionStorage.setItem(k,'1');location.reload();}"
    "else{console.error('Genapi static assets are stale; please hard refresh.');}}"
    "catch(e){location.reload();}"
)


def web_asset_headers(asset: object) -> dict[str, str]:
    normalized = str(asset).replace("\\", "/")
    if normalized.endswith(".html"):
        return {"Cache-Control": "no-cache, no-store, must-revalidate"}
    if "/_next/static/" in normalized or normalized.startswith("_next/static/"):
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {}


def is_stale_next_script_request(path: str) -> bool:
    return path.startswith("_next/static/") and path.endswith(".js")


def is_reserved_web_fallback_path(path: str) -> bool:
    first_segment = path.split("/", 1)[0]
    return first_segment in {"api", "v1", "auth"}


def has_file_extension(path: str) -> bool:
    return bool(PurePosixPath(path).suffix)


def stale_next_script_response() -> Response:
    return Response(
        STALE_NEXT_SCRIPT,
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def create_app() -> FastAPI:
    app_version = config.app_version

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = Event()
        limited_account_thread = start_limited_account_watcher(stop_event)
        image_cache_thread = start_image_cache_watcher(stop_event)
        config.cleanup_old_images()
        try:
            yield
        finally:
            stop_event.set()
            limited_account_thread.join(timeout=1)
            image_cache_thread.join(timeout=1)

    app = FastAPI(title="Genapi", version=app_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def reject_oversized_image_edit_requests(request: Request, call_next):
        if request.url.path == "/api/image/edits":
            content_length = request.headers.get("content-length", "").strip()
            try:
                request_size = int(content_length) if content_length else 0
            except ValueError:
                request_size = 0
            if request_size > ai.image_edit_max_request_size_bytes():
                return JSONResponse(status_code=413, content={"detail": {"error": "image files are too large"}})
        return await call_next(request)

    app.include_router(ai.create_router())
    app.include_router(gallery.create_router())
    app.include_router(accounts.create_router())
    app.include_router(register.create_router())
    app.include_router(system.create_router(app_version))
    app.include_router(update.create_router(app_version))
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

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve_web(full_path: str):
        normalized_path = full_path.strip("/")
        if is_reserved_web_fallback_path(normalized_path):
            raise HTTPException(status_code=404, detail="Not Found")
        asset = resolve_web_asset(full_path)
        if asset is not None:
            return FileResponse(asset, headers=web_asset_headers(asset))
        if is_stale_next_script_request(normalized_path):
            return stale_next_script_response()
        if normalized_path.startswith("_next/") or has_file_extension(normalized_path):
            raise HTTPException(status_code=404, detail="Not Found")
        fallback = resolve_web_asset("")
        if fallback is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(fallback, headers=web_asset_headers(fallback))

    return app
