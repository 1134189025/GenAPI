from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
import urllib.request
import uuid


DEFAULT_REPO = "1134189025/GenAPI"
DEFAULT_SERVICE = "app"
DEFAULT_HELPER_IMAGE = "docker:28-cli"
DEFAULT_TIMEOUT_SECONDS = 900
STATUS_CACHE_SECONDS = 300
_STATUS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass
class UpdateSettings:
    enabled: bool = False
    repo: str = DEFAULT_REPO
    service: str = DEFAULT_SERVICE
    compose_dir: str = ""
    helper_image: str = DEFAULT_HELPER_IMAGE
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    health_url: str = ""
    github_token: str = ""


def load_update_settings() -> UpdateSettings:
    return UpdateSettings(
        enabled=_read_bool("GENAPI_ENABLE_WEB_UPDATER", False),
        repo=os.environ.get("GENAPI_UPDATE_REPO", DEFAULT_REPO),
        service=os.environ.get("GENAPI_UPDATE_SERVICE", DEFAULT_SERVICE),
        compose_dir=os.environ.get("GENAPI_UPDATE_COMPOSE_DIR", ""),
        helper_image=os.environ.get("GENAPI_UPDATE_HELPER_IMAGE", DEFAULT_HELPER_IMAGE),
        timeout_seconds=_read_int("GENAPI_UPDATE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        health_url=os.environ.get("GENAPI_UPDATE_HEALTH_URL", ""),
        github_token=os.environ.get("GITHUB_TOKEN", ""),
    )


def build_release_status(
    current_version: str,
    settings: UpdateSettings,
    opener: Callable[..., Any] = urllib.request.urlopen,
    force: bool = False,
) -> dict[str, Any]:
    checked_at = _utc_now()
    if not settings.enabled:
        return {
            "enabled": False,
            "mode": "manual",
            "update_available": False,
            "disabled_reason": "Set GENAPI_ENABLE_WEB_UPDATER=true to enable web updates.",
            "current_version": current_version,
            "checked_at": checked_at,
        }

    cache_key = _status_cache_key(current_version, settings)
    if not force and (cached := _STATUS_CACHE.get(cache_key)) is not None:
        cached_at, cached_status = cached
        if time.time() - cached_at < STATUS_CACHE_SECONDS:
            return dict(cached_status)

    request = urllib.request.Request(
        f"https://api.github.com/repos/{settings.repo}/releases/latest",
        headers=_github_headers(settings),
    )
    try:
        with opener(request, timeout=settings.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        status = {
            "enabled": True,
            "mode": "docker-compose",
            "current_version": current_version,
            "latest_version": "",
            "latest_tag": "",
            "release_url": "",
            "update_available": False,
            "checked_at": checked_at,
            "repo": settings.repo,
            "service": settings.service,
            "compose_dir": settings.compose_dir,
            "helper_image": settings.helper_image,
            "error": f"Failed to check GitHub Release: {exc}",
        }
        _STATUS_CACHE[cache_key] = (time.time(), dict(status))
        return status

    latest_tag = str(payload.get("tag_name") or "")
    latest_version = _normalize_version(latest_tag)
    release_url = str(payload.get("html_url") or "")

    status = {
        "enabled": True,
        "mode": "docker-compose",
        "current_version": current_version,
        "latest_version": latest_version,
        "latest_tag": latest_tag,
        "release_url": release_url,
        "update_available": _compare_versions(latest_version, current_version) > 0,
        "checked_at": checked_at,
        "repo": settings.repo,
        "service": settings.service,
        "compose_dir": settings.compose_dir,
        "helper_image": settings.helper_image,
    }
    _STATUS_CACHE[cache_key] = (time.time(), dict(status))
    return status


class UpdateJobStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "update-jobs"

    def create(
        self,
        target_version: str,
        target_tag: str,
        release_url: str,
        actor_id: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        job = {
            "id": uuid.uuid4().hex,
            "status": "pending",
            "target_version": target_version,
            "target_tag": target_tag,
            "release_url": release_url,
            "actor_id": actor_id,
            "created_at": now,
            "updated_at": now,
        }
        job_dir = self.jobs_dir / job["id"]
        job_dir.mkdir(parents=True, exist_ok=False)
        _write_json(job_dir / "job.json", job)
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        job_dir = self.jobs_dir / job_id
        job_path = job_dir / "job.json"
        if not job_path.exists():
            return None
        return self._load_job(job_dir)

    def update(self, job_id: str, **updates: Any) -> dict[str, Any]:
        job_dir = self.jobs_dir / job_id
        job_path = job_dir / "job.json"
        if not job_path.exists():
            raise KeyError(job_id)
        job = _read_json(job_path)
        job.update(updates)
        job["updated_at"] = _utc_now()
        _write_json(job_path, job)
        return self._load_job(job_dir)

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.jobs_dir.exists():
            return []

        jobs: list[dict[str, Any]] = []
        for job_path in self.jobs_dir.glob("*/job.json"):
            jobs.append(self._load_job(job_path.parent))

        jobs.sort(key=lambda job: str(job.get("created_at") or ""), reverse=True)
        return jobs[:limit]

    def has_active_job(self) -> bool:
        return any(str(job.get("status")) in _ACTIVE_JOB_STATUSES for job in self.list_recent(limit=1000))

    def _load_job(self, job_dir: Path) -> dict[str, Any]:
        job = _read_json(job_dir / "job.json")

        status_path = job_dir / "status.json"
        if status_path.exists():
            job.update(_read_json(status_path))

        log_path = job_dir / "log.txt"
        if log_path.exists():
            job["logs"] = log_path.read_text(encoding="utf-8").splitlines()
        else:
            job["logs"] = []

        return job


_ACTIVE_JOB_STATUSES = {"pending", "running", "in_progress", "updating"}
_VERSION_RE = re.compile(r"^v?(?P<version>\d+(?:\.\d+){0,2})")


def _read_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _github_headers(settings: UpdateSettings) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Genapi-Web-Updater",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _status_cache_key(current_version: str, settings: UpdateSettings) -> str:
    return "|".join(
        [
            current_version,
            settings.repo,
            settings.service,
            settings.compose_dir,
            settings.helper_image,
            settings.health_url,
        ]
    )


def _normalize_version(version_or_tag: str) -> str:
    match = _VERSION_RE.match(version_or_tag.strip())
    if not match:
        return version_or_tag.strip().lstrip("v")
    return match.group("version")


def _compare_versions(left: str, right: str) -> int:
    left_parts = _version_tuple(left)
    right_parts = _version_tuple(right)
    return (left_parts > right_parts) - (left_parts < right_parts)


def _version_tuple(value: str) -> tuple[int, int, int]:
    normalized = _normalize_version(value)
    parts = [int(part) for part in normalized.split(".") if part.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
