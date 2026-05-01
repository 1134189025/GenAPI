from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from fastapi.concurrency import run_in_threadpool

from api.support import require_admin
from services.config import DATA_DIR
try:
    from services.update_executor import BinaryUpdateExecutor, DockerComposeUpdateExecutor
except ImportError:
    BinaryUpdateExecutor = None
    DockerComposeUpdateExecutor = None
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
        status, preflight = await run_in_threadpool(lambda: _status_with_preflight(settings, status))
        return {
            "status": status,
            "preflight": preflight,
            "jobs": await run_in_threadpool(store.list_recent),
        }

    @router.get("/api/admin/system/check-updates")
    async def check_system_updates(force: bool = False, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        settings = load_update_settings()
        status = await run_in_threadpool(
            lambda: build_release_status(current_version=app_version, settings=settings, force=force)
        )
        status, _preflight = await run_in_threadpool(lambda: _status_with_preflight(settings, status))
        return status

    @router.post("/api/admin/system/update")
    async def update_system(authorization: str | None = Header(default=None)):
        identity = require_admin(authorization)
        result = await _run_update(app_version, identity)
        return result

    @router.post("/api/admin/system/rollback")
    async def rollback_system(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        settings = load_update_settings()
        executor = _create_executor(settings)
        rollback = _required_executor_method(executor, "rollback")
        preflight = await run_in_threadpool(lambda: _executor_preflight(executor, allow_pending_current=True))
        if not preflight.get("ok"):
            raise HTTPException(status_code=400, detail={"error": "rollback preflight failed", "preflight": preflight})
        await run_in_threadpool(rollback)
        return {"message": "Rollback completed successfully. Restart is required.", "need_restart": True}

    @router.post("/api/admin/system/restart")
    async def restart_system(background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        settings = load_update_settings()
        executor = _create_executor(settings)
        restart = _required_executor_method(executor, "restart")
        preflight = await run_in_threadpool(lambda: _executor_preflight(executor, allow_pending_current=True))
        if not preflight.get("ok"):
            raise HTTPException(status_code=400, detail={"error": "restart preflight failed", "preflight": preflight})
        background_tasks.add_task(restart)
        return {"message": "Restart scheduled."}

    @router.post("/api/admin/update/start")
    async def start_update(authorization: str | None = Header(default=None)):
        identity = require_admin(authorization)
        result = await _run_update(app_version, identity)
        return {"job": result["job"]}

    @router.get("/api/admin/update/jobs/{job_id}")
    async def get_update_job(job_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        store = UpdateJobStore(DATA_DIR)
        job = await run_in_threadpool(lambda: store.get(job_id))
        if job is None:
            raise HTTPException(status_code=404, detail={"error": "update job not found"})
        return {"job": job}

    return router


async def _run_update(app_version: str, identity: dict[str, object]) -> dict[str, Any]:
    settings = load_update_settings()
    store = UpdateJobStore(DATA_DIR)

    status = await run_in_threadpool(
        lambda: build_release_status(current_version=app_version, settings=settings, force=True)
    )
    _validate_update_status(status)

    executor = _create_executor(settings)
    release_info = _release_info(status)
    preflight = await run_in_threadpool(lambda: _executor_preflight(executor, release_info=release_info))
    if not preflight.get("ok"):
        raise HTTPException(status_code=400, detail={"error": "update preflight failed", "preflight": preflight})

    job = await run_in_threadpool(
        lambda: store.create_if_idle(
            target_version=_target_version(status),
            target_tag=_target_tag(status),
            release_url=_release_url(status),
            actor_id=str(identity.get("id") or identity.get("subject_id") or ""),
        )
    )
    if job is None:
        raise HTTPException(status_code=409, detail={"error": "an update job is already running"})

    job_id = str(job["id"])
    try:
        job = await run_in_threadpool(lambda: store.update(job_id, status="running"))
        update_result = await run_in_threadpool(lambda: _perform_update(executor, job, status))
    except HTTPException:
        raise
    except Exception as exc:
        safe_error = redact_sensitive_text(str(exc))
        await run_in_threadpool(lambda: store.update(job_id, status="failed", error=safe_error))
        raise HTTPException(status_code=500, detail={"error": safe_error}) from exc

    if isinstance(update_result, dict) and update_result.get("async"):
        message = str(update_result.get("message") or "Docker update started.")
        helper_container_id = str(update_result.get("container_id") or update_result.get("container") or "")
        job_updates: dict[str, Any] = {
            "status": "running",
            "message": message,
            "error": "",
            "async_update": True,
        }
        if helper_container_id:
            job_updates["helper_container_id"] = helper_container_id
        job = await run_in_threadpool(lambda: store.update(job_id, **job_updates))
        return {"message": message, "need_restart": bool(update_result.get("need_restart", False)), "job": job}

    job = await run_in_threadpool(lambda: store.update(job_id, status="succeeded", error=""))
    return {"message": "Update completed successfully. Restart is required.", "need_restart": True, "job": job}


def _validate_update_status(status: dict[str, Any]) -> None:
    if not status.get("enabled"):
        raise HTTPException(
            status_code=400,
            detail={"error": status.get("disabled_reason") or "system updater is unavailable"},
        )
    if status.get("error"):
        raise HTTPException(status_code=400, detail={"error": redact_sensitive_text(str(status.get("error")))})
    if not status.get("update_available"):
        raise HTTPException(status_code=400, detail={"error": "no update is available"})
    if status.get("can_update") is False:
        reason = status.get("disabled_reason") or status.get("warning") or status.get("reason") or "system cannot be updated"
        raise HTTPException(status_code=400, detail={"error": str(reason)})


def _create_executor(settings: object) -> object:
    deployment_mode = str(getattr(settings, "deployment_mode", "") or "")
    build_type = str(getattr(settings, "build_type", "") or "")
    if deployment_mode == "docker" and build_type == "docker":
        if DockerComposeUpdateExecutor is None:
            raise HTTPException(status_code=400, detail={"error": "Docker updater is not available"})
        return DockerComposeUpdateExecutor(DATA_DIR, settings)
    if BinaryUpdateExecutor is None:
        raise HTTPException(status_code=400, detail={"error": "binary updater is not available"})
    return BinaryUpdateExecutor(DATA_DIR, settings)


def _executor_preflight(
    executor: object,
    *,
    release_info: dict[str, Any] | None = None,
    allow_pending_current: bool = False,
) -> dict[str, Any]:
    method = _required_executor_method(executor, "preflight")
    result = _call_with_supported_kwargs(
        method,
        {
            "release_info": release_info,
            "allow_pending_current": allow_pending_current,
        },
    )
    return result if isinstance(result, dict) else {"ok": False, "errors": ["invalid updater preflight response"], "warnings": []}


def _status_with_preflight(settings: object, status: dict[str, Any]) -> tuple[dict[str, Any], dict[str, object]]:
    preflight = _executor_preflight(_create_executor(settings), release_info=_release_info(status))
    next_status = dict(status)
    next_status["preflight"] = preflight
    deployment_mode = str(getattr(settings, "deployment_mode", "") or "")
    build_type = str(getattr(settings, "build_type", "") or "")
    if deployment_mode == "docker" and build_type == "docker":
        next_status["can_update"] = bool(next_status.get("update_available")) and bool(preflight.get("ok"))
    if next_status.get("update_available") and not preflight.get("ok") and (
        next_status.get("can_update") or (deployment_mode == "docker" and build_type == "docker")
    ):
        next_status["can_update"] = False
        errors = [str(error) for error in preflight.get("errors", []) if str(error).strip()]
        next_status["disabled_reason"] = "; ".join(errors) or "update preflight failed"
    return next_status, preflight


def _target_version(status: dict[str, Any]) -> str:
    return str(status.get("latest_version") or status.get("target_version") or status.get("version") or "")


def _target_tag(status: dict[str, Any]) -> str:
    return str(status.get("latest_tag") or status.get("target_tag") or status.get("tag") or _target_version(status))


def _release_url(status: dict[str, Any]) -> str:
    return str(status.get("release_url") or status.get("html_url") or "")


def _perform_update(executor: object, job: dict[str, Any], status: dict[str, Any]) -> Any:
    method = _required_executor_method(executor, "perform_update")
    kwargs = {
        "job_id": str(job.get("id") or ""),
        "target_tag": str(job.get("target_tag") or ""),
        "target_version": str(job.get("target_version") or ""),
        "release_url": str(job.get("release_url") or ""),
        "release_info": _release_info(status),
        "job": job,
        "status": status,
        "release_status": status,
    }
    return _call_with_supported_kwargs(method, kwargs)


def _release_info(status: dict[str, Any]) -> dict[str, Any]:
    release_info = status.get("release_info")
    return release_info if isinstance(release_info, dict) else {}


def _required_executor_method(executor: object, name: str) -> Callable[..., Any]:
    method = getattr(executor, name, None)
    if not callable(method):
        raise HTTPException(status_code=400, detail={"error": f"updater does not support {name}"})
    return method


def _call_with_supported_kwargs(method: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(**kwargs)

    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return method(**kwargs)

    supported = {
        name: value
        for name, value in kwargs.items()
        if name in parameters
    }
    missing_required = [
        name
        for name, parameter in parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        and name not in supported
    ]
    if missing_required:
        return method()
    return method(**supported)
