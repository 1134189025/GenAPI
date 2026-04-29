from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import tempfile
from typing import Any, Callable
import urllib.request
import uuid


DEFAULT_REPO = "1134189025/GenAPI"
DEFAULT_SERVICE = "app"
DEFAULT_HELPER_IMAGE = "docker:28-cli"
DEFAULT_TIMEOUT_SECONDS = 900
STATUS_CACHE_SECONDS = 300
ACTIVE_LOCK_CREATE_GRACE_SECONDS = 30
ACTIVE_LOCK_ORPHAN_STALE_SECONDS = 300
ACTIVE_JOB_STALE_SECONDS = DEFAULT_TIMEOUT_SECONDS * 2
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
    def __init__(self, data_dir: str | Path, *, active_job_stale_seconds: int = ACTIVE_JOB_STALE_SECONDS):
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "update-jobs"
        self.active_lock_path = self.jobs_dir / ".active.lock"
        self.active_job_stale_seconds = max(1, int(active_job_stale_seconds or ACTIVE_JOB_STALE_SECONDS))

    def create(
        self,
        target_version: str,
        target_tag: str,
        release_url: str,
        actor_id: str,
    ) -> dict[str, Any]:
        return self._create_unlocked(
            target_version=target_version,
            target_tag=target_tag,
            release_url=release_url,
            actor_id=actor_id,
        )

    def create_if_idle(
        self,
        target_version: str,
        target_tag: str,
        release_url: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        if not self._acquire_active_lock():
            if not self._recover_stale_active_lock():
                return None
            if not self._acquire_active_lock():
                return None
        if self.has_active_job():
            self._release_active_lock()
            return None
        try:
            job = self._create_unlocked(
                target_version=target_version,
                target_tag=target_tag,
                release_url=release_url,
                actor_id=actor_id,
            )
            self._record_active_lock_job(str(job.get("id") or ""))
            return job
        except Exception:
            self._release_active_lock()
            raise

    def _create_unlocked(
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
        job = self._load_job(job_dir)
        if str(job.get("status")) not in _ACTIVE_JOB_STATUSES:
            self._release_active_lock_if_no_active_jobs()
        return job

    def update(self, job_id: str, **updates: Any) -> dict[str, Any]:
        job_dir = self.jobs_dir / job_id
        job_path = job_dir / "job.json"
        if not job_path.exists():
            raise KeyError(job_id)
        job = _read_json(job_path)
        job.update(updates)
        job["updated_at"] = _utc_now()
        _write_json(job_path, job)
        if str(job.get("status")) not in _ACTIVE_JOB_STATUSES:
            self._release_active_lock()
        return self._load_job(job_dir)

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.jobs_dir.exists():
            return []

        jobs: list[dict[str, Any]] = []
        for job_path in self.jobs_dir.glob("*/job.json"):
            jobs.append(self._load_job(job_path.parent))

        jobs.sort(key=lambda job: str(job.get("created_at") or ""), reverse=True)
        if jobs and not any(str(job.get("status")) in _ACTIVE_JOB_STATUSES for job in jobs):
            self._release_active_lock()
        return jobs[:limit]

    def has_active_job(self) -> bool:
        return self._has_active_job_unlocked()

    def _acquire_active_lock(self) -> bool:
        try:
            fd = os.open(str(self.active_lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"created_at": _utc_now()}, handle, sort_keys=True)
        return True

    def _record_active_lock_job(self, job_id: str) -> None:
        if not job_id:
            return
        try:
            fd = os.open(str(self.active_lock_path), os.O_WRONLY | os.O_TRUNC)
        except FileNotFoundError:
            return
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"created_at": _utc_now(), "job_id": job_id}, handle, sort_keys=True)

    def _release_active_lock(self) -> None:
        try:
            self.active_lock_path.unlink()
        except FileNotFoundError:
            pass

    def _release_active_lock_if_no_active_jobs(self) -> None:
        if not self._has_active_job_unlocked():
            self._release_active_lock()

    def _recover_stale_active_lock(self) -> bool:
        if not self.active_lock_path.exists():
            return False
        self._recover_stale_active_jobs_unlocked()
        if self._has_active_job_unlocked():
            return False

        lock = self._read_active_lock_state()
        job_id = str(lock.get("job_id") or "").strip()
        if job_id:
            self._release_active_lock()
            return True

        if bool(lock.get("legacy")) and self._has_any_job_unlocked():
            self._release_active_lock()
            return True

        age_seconds = self._active_lock_age_seconds(lock)
        threshold = ACTIVE_LOCK_ORPHAN_STALE_SECONDS
        if bool(lock.get("creating")):
            threshold = ACTIVE_LOCK_CREATE_GRACE_SECONDS
        if age_seconds is not None and age_seconds >= threshold:
            self._release_active_lock()
            return True
        return False

    def _has_any_job_unlocked(self) -> bool:
        return self.jobs_dir.exists() and any(self.jobs_dir.glob("*/job.json"))

    def _has_active_job_unlocked(self) -> bool:
        if not self.jobs_dir.exists():
            return False
        for job_path in self.jobs_dir.glob("*/job.json"):
            job = self._load_job(job_path.parent)
            if str(job.get("status")) in _ACTIVE_JOB_STATUSES:
                return True
        return False

    def _recover_stale_active_jobs_unlocked(self) -> int:
        if not self.jobs_dir.exists():
            return 0
        recovered = 0
        current = _parse_utc(_utc_now()) or datetime.now(timezone.utc)
        for job_path in self.jobs_dir.glob("*/job.json"):
            job = self._load_job(job_path.parent)
            if str(job.get("status")) not in _ACTIVE_JOB_STATUSES:
                continue
            updated_at = (
                _parse_utc(str(job.get("updated_at") or ""))
                or _parse_utc(str(job.get("created_at") or ""))
            )
            if updated_at is None:
                continue
            if (current - updated_at).total_seconds() < self.active_job_stale_seconds:
                continue
            job.update(
                {
                    "status": "failed",
                    "error": "stale active update job recovered after timeout",
                    "updated_at": _utc_now(),
                }
            )
            _write_json(job_path, job)
            recovered += 1
        return recovered

    def _read_active_lock_state(self) -> dict[str, Any]:
        try:
            text = self.active_lock_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return {}
        if not text:
            return {"creating": True}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"legacy": True, "created_at": text}
        if not isinstance(payload, dict):
            return {"legacy": True}
        return payload

    def _active_lock_age_seconds(self, lock: dict[str, Any]) -> float | None:
        created_at = _parse_utc(str(lock.get("created_at") or ""))
        if created_at is None:
            try:
                created_at = datetime.fromtimestamp(self.active_lock_path.stat().st_mtime, timezone.utc)
            except FileNotFoundError:
                return None
        return (datetime.now(timezone.utc) - created_at).total_seconds()

    def _load_job(self, job_dir: Path) -> dict[str, Any]:
        job = _read_json(job_dir / "job.json")

        status_path = job_dir / "status.json"
        if status_path.exists():
            try:
                job.update(_read_json(status_path))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                job["status_error"] = f"Unable to read helper status: {exc}"

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


def _parse_utc(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
