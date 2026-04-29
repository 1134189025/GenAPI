from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool

from api.support import require_admin
from services.config import DATA_DIR
from services.update_executor import DockerUpdateExecutor
from services.update_service import UpdateJobStore, build_release_status, load_update_settings
from utils.helper import redact_sensitive_text


def create_router(app_version: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/admin/update/status")
    async def get_update_status(force: bool = False, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        settings = load_update_settings()
        store = UpdateJobStore(DATA_DIR)
        status = await run_in_threadpool(
            lambda: build_release_status(current_version=app_version, settings=settings, force=force)
        )
        preflight = await run_in_threadpool(lambda: DockerUpdateExecutor(DATA_DIR, settings).preflight())
        return {
            "status": status,
            "preflight": preflight,
            "jobs": await run_in_threadpool(store.list_recent),
        }

    @router.post("/api/admin/update/start")
    async def start_update(authorization: str | None = Header(default=None)):
        identity = require_admin(authorization)
        settings = load_update_settings()
        store = UpdateJobStore(DATA_DIR)

        status = await run_in_threadpool(
            lambda: build_release_status(current_version=app_version, settings=settings, force=True)
        )
        if not status.get("enabled"):
            raise HTTPException(status_code=400, detail={"error": status.get("disabled_reason") or "web updater is disabled"})
        if status.get("error"):
            raise HTTPException(status_code=502, detail={"error": redact_sensitive_text(str(status.get("error")))})
        if not status.get("update_available"):
            raise HTTPException(status_code=400, detail={"error": "no newer GitHub Release is available"})

        executor = DockerUpdateExecutor(DATA_DIR, settings)
        preflight = await run_in_threadpool(executor.preflight)
        if not preflight.get("ok"):
            raise HTTPException(status_code=400, detail={"error": "update preflight failed", "preflight": preflight})

        job = store.create_if_idle(
            target_version=str(status.get("latest_version") or ""),
            target_tag=str(status.get("latest_tag") or ""),
            release_url=str(status.get("release_url") or ""),
            actor_id=str(identity.get("id") or identity.get("subject_id") or ""),
        )
        if job is None:
            raise HTTPException(status_code=409, detail={"error": "an update job is already running"})
        try:
            await run_in_threadpool(
                lambda: executor.start(
                    job_id=str(job["id"]),
                    target_tag=str(job["target_tag"]),
                    target_version=str(job["target_version"]),
                    release_url=str(job["release_url"]),
                )
            )
        except Exception as exc:
            safe_error = redact_sensitive_text(str(exc))
            store.update(job["id"], status="failed", error=safe_error)
            raise HTTPException(status_code=500, detail={"error": safe_error}) from exc

        job = store.update(job["id"], status="running")
        return {"job": job}

    @router.get("/api/admin/update/jobs/{job_id}")
    async def get_update_job(job_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        store = UpdateJobStore(DATA_DIR)
        job = await run_in_threadpool(lambda: store.get(job_id))
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "update job not found"})
        return {"job": job}

    return router
