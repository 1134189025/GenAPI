from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import io
import json
import os
import platform
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sys
import tarfile
import tempfile
import time
from typing import Any, Callable
from urllib.parse import quote, urlparse
import urllib.request


UrlOpener = Callable[..., Any]
ExitFunc = Callable[[int], Any]
ALLOWED_DOWNLOAD_HOSTS = ("github.com", "objects.githubusercontent.com")
BINARY_NAMES = {"genapi", "genapi.exe"}
CHECKSUM_RE = re.compile(r"^[a-fA-F0-9]{64}$")
DOCKER_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
DOCKER_COMPOSE_FILE_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


@dataclass(frozen=True)
class ReleaseLayout:
    app_dir: Path
    releases_dir: Path
    current_link: Path
    previous_link: Path
    running_release_dir: Path


class BinaryUpdateExecutor:
    def __init__(
        self,
        data_dir: Path | str,
        settings: Any,
        opener: UrlOpener = urllib.request.urlopen,
        executable_path: Path | str | None = None,
        exit_func: ExitFunc | None = None,
        restart_delay_seconds: float = 0.5,
        systemctl_runner: Any | None = None,
        docker_client_factory: Any | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings
        self._opener = opener
        self._executable_path = Path(executable_path) if executable_path is not None else None
        self._exit_func = exit_func or os._exit
        self._restart_delay_seconds = max(0.0, float(restart_delay_seconds or 0.0))
        self._docker_client_factory = docker_client_factory

    def preflight(self, release_info: dict[str, Any] | None = None, allow_pending_current: bool = False) -> dict[str, object]:
        errors: list[str] = []
        warnings: list[str] = []

        deployment_mode = str(getattr(self.settings, "deployment_mode", "") or "")
        if deployment_mode != "systemd-binary":
            errors.append("GENAPI_DEPLOYMENT_MODE must be systemd-binary for binary updates.")

        build_type = str(getattr(self.settings, "build_type", "") or "")
        if build_type != "release":
            errors.append("GENAPI_BUILD_TYPE must be release for binary updates.")

        executable = self._resolve_executable()
        if not executable:
            errors.append("Current executable could not be resolved.")
        elif not executable.exists():
            errors.append(f"Current executable does not exist: {executable}")
        elif not executable.is_file():
            errors.append(f"Current executable is not a regular file: {executable}")
        else:
            try:
                layout = self._release_layout(executable, allow_pending_current=allow_pending_current)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                if not os.access(layout.app_dir, os.W_OK):
                    errors.append(f"Release app directory is not writable: {layout.app_dir}")
                if not os.access(layout.releases_dir, os.W_OK):
                    errors.append(f"Release versions directory is not writable: {layout.releases_dir}")

        if platform.system().lower() != "linux":
            warnings.append("Binary updates are designed for Linux systemd deployments.")

        if release_info is not None and self._compatible_asset(release_info) is None:
            errors.append("No compatible linux_amd64/linux_arm64 release asset was found for this runtime.")
        if release_info is not None and self._checksum_asset(release_info) is None:
            errors.append("Release checksums.txt asset is required for binary updates.")

        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def perform_update(self, target_tag: str, target_version: str, release_info: dict[str, Any]) -> dict[str, Any]:
        preflight = self.preflight(release_info=release_info)
        if not preflight.get("ok"):
            raise RuntimeError("update preflight failed: " + "; ".join(str(error) for error in preflight["errors"]))

        asset = self._compatible_asset(release_info)
        if asset is None:
            raise ValueError("No compatible release asset found.")

        asset_name = str(asset.get("name") or "")
        asset_url = str(asset.get("download_url") or "")
        asset_size = self._asset_size(asset)
        max_bytes = self._max_download_bytes()
        if asset_size > max_bytes:
            raise ValueError(f"Release asset {asset_name} exceeds max download size of {max_bytes} bytes.")

        checksum_asset = self._checksum_asset(release_info)
        if checksum_asset is None:
            raise ValueError("Release checksums.txt asset is required for binary updates.")

        archive_bytes = self._download_url(asset_url, max_bytes=max_bytes, expected_size=asset_size)
        checksum_bytes = self._download_url(
            str(checksum_asset.get("download_url") or ""),
            max_bytes=max_bytes,
            expected_size=self._asset_size(checksum_asset),
        )
        self._verify_checksum(asset_name, archive_bytes, checksum_bytes)

        executable = self._require_executable()
        layout = self._require_release_layout(executable)
        with tempfile.TemporaryDirectory(prefix=f".{executable.name}-update-", dir=layout.releases_dir) as temp_dir:
            staged_payload = Path(temp_dir) / "payload"
            self._extract_release_payload(archive_bytes, staged_payload)
            backup_path = self._activate_release_payload(layout, staged_payload, target_version)

        return {
            "ok": True,
            "target_tag": target_tag,
            "target_version": target_version,
            "asset": {
                "name": asset_name,
                "download_url": asset_url,
                "size": asset_size,
            },
            "backup_path": str(backup_path),
        }

    def rollback(self) -> dict[str, Any]:
        executable = self._require_executable()
        layout = self._require_release_layout(executable, allow_pending_current=True)
        if not layout.previous_link.is_symlink():
            raise FileNotFoundError(f"Previous release symlink does not exist: {layout.previous_link}")
        previous_target = layout.previous_link.resolve()
        _validate_release_payload_tree(previous_target)

        current_target = layout.current_link.resolve()
        current_tmp = layout.app_dir / f".current-rollback-{os.getpid()}"
        previous_tmp = layout.app_dir / f".previous-rollback-{os.getpid()}"
        self._remove_path(current_tmp)
        self._remove_path(previous_tmp)
        current_tmp.symlink_to(previous_target)
        os.replace(current_tmp, layout.current_link)
        try:
            previous_tmp.symlink_to(current_target)
            os.replace(previous_tmp, layout.previous_link)
        finally:
            self._remove_path(current_tmp)
            self._remove_path(previous_tmp)
        return {"ok": True, "executable": str(executable), "restored_from": str(previous_target)}

    def restart(self) -> dict[str, Any]:
        if self._restart_delay_seconds:
            time.sleep(self._restart_delay_seconds)
        self._exit_func(0)
        return {"ok": True, "message": "process exit requested for systemd restart"}

    def start(self, job_id: str, target_tag: str, target_version: str, release_url: str) -> dict[str, Any]:
        job_dir = self.data_dir / "update-jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        self._write_job_status(
            job_dir,
            status="running",
            message="Starting binary update",
            target_tag=target_tag,
            target_version=target_version,
            release_url=release_url,
        )
        try:
            release_info = self._fetch_release_info(target_tag)
            result = self.perform_update(target_tag, target_version, release_info)
            self._write_job_status(
                job_dir,
                status="succeeded",
                message="Update completed successfully; restart is required",
                target_tag=target_tag,
                target_version=target_version,
                release_url=release_url,
                finished=True,
            )
            return result
        except Exception as exc:
            self._write_job_status(
                job_dir,
                status="failed",
                message=str(exc),
                target_tag=target_tag,
                target_version=target_version,
                release_url=release_url,
                finished=True,
            )
            raise

    def _resolve_executable(self) -> Path | None:
        if self._executable_path is not None:
            candidate = self._executable_path
        else:
            proc_exe = Path("/proc/self/exe")
            candidate = proc_exe if proc_exe.exists() else Path(sys.executable)
        try:
            return candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            return None

    def _require_executable(self) -> Path:
        executable = self._resolve_executable()
        if executable is None:
            raise RuntimeError("Current executable could not be resolved.")
        return executable

    def _require_release_layout(self, executable: Path, *, allow_pending_current: bool = False) -> ReleaseLayout:
        try:
            return self._release_layout(executable, allow_pending_current=allow_pending_current)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    @staticmethod
    def _release_layout(executable: Path, *, allow_pending_current: bool = False) -> ReleaseLayout:
        running_release_dir = executable.parent
        releases_dir = running_release_dir.parent
        app_dir = releases_dir.parent
        current_link = app_dir / "current"
        previous_link = app_dir / "previous"

        if releases_dir.name != "releases":
            raise ValueError("Current executable must run from the release layout under releases/<version>/genapi.")
        if not current_link.is_symlink():
            raise ValueError(f"Release current symlink is required: {current_link}")
        current_target = current_link.resolve()
        if current_target != running_release_dir:
            previous_matches = previous_link.is_symlink() and previous_link.resolve() == running_release_dir
            if not (allow_pending_current and previous_matches):
                raise ValueError("Release current symlink does not point at the running executable; restart is required before another update.")
        if not releases_dir.is_dir():
            raise ValueError(f"Release versions directory does not exist: {releases_dir}")
        return ReleaseLayout(
            app_dir=app_dir,
            releases_dir=releases_dir,
            current_link=current_link,
            previous_link=previous_link,
            running_release_dir=running_release_dir,
        )

    def _compatible_asset(self, release_info: dict[str, Any]) -> dict[str, Any] | None:
        marker = self._runtime_asset_marker()
        if marker is None:
            return None
        assets = release_info.get("assets")
        if not isinstance(assets, list):
            return None
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "").lower()
            if not name or name.endswith("checksums.txt"):
                continue
            if marker in name:
                return asset
        return None

    def _checksum_asset(self, release_info: dict[str, Any]) -> dict[str, Any] | None:
        assets = release_info.get("assets")
        if not isinstance(assets, list):
            return None
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("name") or "").lower().endswith("checksums.txt"):
                return asset
        return None

    def _download_url(self, url: str, *, max_bytes: int, expected_size: int = 0) -> bytes:
        self._validate_download_url(url)
        if expected_size > max_bytes:
            raise ValueError(f"Download exceeds max download size of {max_bytes} bytes.")

        request = urllib.request.Request(url, headers=self._download_headers())
        chunks: list[bytes] = []
        total = 0
        with self._opener(request, timeout=self._timeout_seconds()) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Download exceeds max download size of {max_bytes} bytes.")
                chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _validate_download_url(url: str) -> None:
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            raise ValueError("Download URL is invalid.") from exc
        if parsed.scheme != "https":
            raise ValueError("Download URL must use HTTPS.")
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if not hostname:
            raise ValueError("Download URL must include a hostname.")
        if not any(hostname == allowed or hostname.endswith(f".{allowed}") for allowed in ALLOWED_DOWNLOAD_HOSTS):
            raise ValueError(f"Download host is not allowed: {hostname}")

    @staticmethod
    def _verify_checksum(asset_name: str, payload: bytes, checksum_payload: bytes) -> None:
        expected = _checksum_for_asset(asset_name, checksum_payload.decode("utf-8", errors="replace"))
        if expected is None:
            raise ValueError(f"checksums.txt does not contain a checksum for {asset_name}.")
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual.lower(), expected.lower()):
            raise ValueError(f"Release asset checksum mismatch for {asset_name}.")

    @staticmethod
    def _extract_release_payload(archive_bytes: bytes, destination: Path) -> None:
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
                members = archive.getmembers()
                for member in members:
                    _validate_tar_path(member.name)
                    if not (member.isfile() or member.isdir()):
                        raise ValueError(f"Archive member {member.name} is not a regular file or directory.")

                payload_root = _find_release_payload_root(members)
                destination.mkdir(parents=True, exist_ok=True)
                for member in members:
                    member_path = PurePosixPath(member.name)
                    relative_path = _relative_to_payload_root(member_path, payload_root)
                    if relative_path is None or str(relative_path) in {"", "."}:
                        continue
                    target = destination.joinpath(*relative_path.parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError(f"Archive member {member.name} could not be read.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source, target.open("wb") as handle:
                        handle.write(source.read())
                    target.chmod(member.mode & 0o777 or 0o644)
        except tarfile.TarError as exc:
            raise ValueError("Release asset is not a readable tar archive.") from exc

        _validate_release_payload_tree(destination)

    @staticmethod
    def _activate_release_payload(layout: ReleaseLayout, staged_payload: Path, target_version: str) -> Path:
        release_dir = Path(tempfile.mkdtemp(prefix=f"{_safe_release_dir_prefix(target_version)}-", dir=layout.releases_dir))
        current_tmp = layout.app_dir / f".current-{os.getpid()}"
        previous_tmp = layout.app_dir / f".previous-{os.getpid()}"
        old_release_dir = layout.current_link.resolve()
        try:
            for name in ("genapi", "VERSION", "web_dist"):
                os.replace(staged_payload / name, release_dir / name)
            (release_dir / "genapi").chmod(0o755)
            _validate_release_payload_tree(release_dir)

            BinaryUpdateExecutor._remove_path(current_tmp)
            BinaryUpdateExecutor._remove_path(previous_tmp)
            current_tmp.symlink_to(release_dir)
            previous_tmp.symlink_to(old_release_dir)
            os.replace(previous_tmp, layout.previous_link)
            os.replace(current_tmp, layout.current_link)
        except Exception:
            BinaryUpdateExecutor._remove_path(current_tmp)
            BinaryUpdateExecutor._remove_path(previous_tmp)
            shutil.rmtree(release_dir, ignore_errors=True)
            raise
        return layout.previous_link

    @staticmethod
    def _backup_path(executable: Path) -> Path:
        return Path(f"{executable}.backup")

    @staticmethod
    def _release_targets(executable: Path) -> list[Path]:
        install_dir = executable.parent
        return [executable, install_dir / "VERSION", install_dir / "web_dist"]

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                return

    @staticmethod
    def _runtime_asset_marker() -> str | None:
        machine = platform.machine().lower()
        if machine in {"x86_64", "amd64"}:
            return "linux_amd64"
        if machine in {"aarch64", "arm64"}:
            return "linux_arm64"
        return None

    def _download_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/octet-stream",
            "User-Agent": "Genapi-Binary-Updater",
        }
        github_token = str(getattr(self.settings, "github_token", "") or "")
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        return headers

    def _timeout_seconds(self) -> int:
        try:
            timeout = int(getattr(self.settings, "timeout_seconds", 900) or 900)
        except (TypeError, ValueError):
            return 900
        return max(1, timeout)

    def _max_download_bytes(self) -> int:
        try:
            max_bytes = int(getattr(self.settings, "max_download_bytes", 524288000) or 524288000)
        except (TypeError, ValueError):
            return 524288000
        return max(1, max_bytes)

    @staticmethod
    def _asset_size(asset: dict[str, Any]) -> int:
        try:
            return max(0, int(asset.get("size") or 0))
        except (TypeError, ValueError):
            return 0

    def _fetch_release_info(self, target_tag: str) -> dict[str, Any]:
        from services.update_service import _release_info_from_payload

        repo = str(getattr(self.settings, "repo", "") or "")
        if not repo:
            raise ValueError("GENAPI_UPDATE_REPO must be set.")
        tag_path = quote(target_tag, safe="")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/tags/{tag_path}",
            headers=self._github_headers(),
        )
        with self._opener(request, timeout=self._timeout_seconds()) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("GitHub release response was not an object.")
        return _release_info_from_payload(payload)

    def _github_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Genapi-Binary-Updater",
        }
        github_token = str(getattr(self.settings, "github_token", "") or "")
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        return headers

    @staticmethod
    def _write_job_status(
        job_dir: Path,
        *,
        status: str,
        message: str,
        target_tag: str,
        target_version: str,
        release_url: str,
        finished: bool = False,
    ) -> None:
        from services.update_service import _utc_now

        payload: dict[str, Any] = {
            "status": status,
            "message": message,
            "target_tag": target_tag,
            "target_version": target_version,
            "release_url": release_url,
            "updated_at": _utc_now(),
        }
        if finished:
            payload["finished_at"] = _utc_now()
        status_path = job_dir / "status.json"
        temp_path = job_dir / f".{status_path.name}.tmp"
        temp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, status_path)
        with (job_dir / "log.txt").open("a", encoding="utf-8") as log:
            log.write(f"{payload['updated_at']} {message}\n")


class DockerComposeUpdateExecutor:
    def __init__(
        self,
        data_dir: Path | str,
        settings: Any,
        docker_client_factory: Any | None = None,
        socket_path: Path | str = "/var/run/docker.sock",
    ) -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings
        self._docker_client_factory = docker_client_factory
        self._socket_path = Path(socket_path)

    def preflight(self, release_info: dict[str, Any] | None = None, allow_pending_current: bool = False) -> dict[str, object]:
        errors: list[str] = []
        warnings: list[str] = []

        deployment_mode = str(getattr(self.settings, "deployment_mode", "") or "")
        if deployment_mode != "docker":
            errors.append("GENAPI_DEPLOYMENT_MODE must be docker for Docker compose updates.")

        build_type = str(getattr(self.settings, "build_type", "") or "")
        if build_type != "docker":
            errors.append("GENAPI_BUILD_TYPE must be docker for Docker compose updates.")

        service = self._service_name()
        if not DOCKER_SERVICE_RE.fullmatch(service):
            errors.append("GENAPI_UPDATE_SERVICE must match [A-Za-z0-9_.-]+.")

        compose_file = self._compose_file()
        raw_compose_dir = self._compose_dir_value()
        compose_dir = self._compose_dir()
        if not raw_compose_dir:
            errors.append("GENAPI_UPDATE_COMPOSE_DIR must be set for Docker compose updates.")
        elif not compose_dir.exists():
            errors.append(f"GENAPI_UPDATE_COMPOSE_DIR does not exist: {compose_dir}")
        elif not compose_dir.is_dir():
            errors.append(f"GENAPI_UPDATE_COMPOSE_DIR is not a directory: {compose_dir}")
        elif not (compose_dir / compose_file).is_file():
            errors.append(f"Docker compose file does not exist: {compose_dir / compose_file}")

        if not self._valid_compose_file(compose_file):
            errors.append("GENAPI_UPDATE_COMPOSE_FILE must be a relative compose file path without '..'.")

        host_compose_dir = self._host_compose_dir_value()
        if not host_compose_dir:
            errors.append("GENAPI_UPDATE_HOST_COMPOSE_DIR must be set for Docker compose updates.")
        elif not Path(host_compose_dir).is_absolute():
            errors.append("GENAPI_UPDATE_HOST_COMPOSE_DIR must be an absolute host path.")

        host_data_dir = self._host_data_dir_value()
        if not host_data_dir:
            errors.append("GENAPI_UPDATE_HOST_DATA_DIR must be set for Docker compose updates.")
        elif not Path(host_data_dir).is_absolute():
            errors.append("GENAPI_UPDATE_HOST_DATA_DIR must be an absolute host path.")

        helper_image = self._helper_image()
        if not helper_image:
            errors.append("GENAPI_UPDATE_HELPER_IMAGE must be set for Docker compose updates.")

        if not self._socket_path.exists():
            errors.append(f"Docker socket is not mounted: {self._socket_path}")
        elif not os.access(self._socket_path, os.R_OK | os.W_OK):
            errors.append(f"Docker socket is not readable and writable: {self._socket_path}")

        if not errors:
            client = None
            try:
                client = self._docker_client()
                client.ping()
            except Exception as exc:
                errors.append(f"Docker daemon is not reachable: {exc}")
            finally:
                self._close_client(client)

        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def perform_update(
        self,
        *,
        job_id: str,
        target_tag: str,
        target_version: str,
        release_url: str,
        **_: Any,
    ) -> dict[str, Any]:
        preflight = self.preflight()
        if not preflight.get("ok"):
            raise RuntimeError("update preflight failed: " + "; ".join(str(error) for error in preflight["errors"]))

        job_dir = self.data_dir / "update-jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        script_path = job_dir / "docker-compose-update.sh"
        service = self._service_name()
        compose_file = self._compose_file()
        helper_project_dir = self._helper_compose_project_dir()
        helper_job_dir = self._helper_job_source_dir(job_id)
        script_path.write_text(
            self._helper_script(
                service=service,
                compose_file=compose_file,
                project_dir=helper_project_dir,
                target_tag=target_tag,
                target_version=target_version,
                release_url=release_url,
            ),
            encoding="utf-8",
        )
        script_path.chmod(0o700)

        client = None
        try:
            client = self._docker_client()
            container = client.containers.run(
                image=self._helper_image(),
                command=["sh", "/job/docker-compose-update.sh"],
                detach=True,
                remove=True,
                name=f"genapi-update-{_safe_container_suffix(job_id)}",
                volumes={
                    str(self._socket_path): {"bind": "/var/run/docker.sock", "mode": "rw"},
                    str(helper_project_dir): {"bind": str(helper_project_dir), "mode": "ro"},
                    str(helper_job_dir): {"bind": "/job", "mode": "rw"},
                },
                working_dir=str(helper_project_dir),
            )
        finally:
            self._close_client(client)

        container_id = _docker_container_id(container)
        return {
            "ok": True,
            "async": True,
            "need_restart": False,
            "message": "Docker update started. The application container will be recreated shortly.",
            "container": container_id or str(container),
            "container_id": container_id,
            "target_tag": target_tag,
            "target_version": target_version,
        }

    def _docker_client(self) -> Any:
        if self._docker_client_factory is not None:
            return self._docker_client_factory()
        import docker

        return docker.from_env()

    @staticmethod
    def _close_client(client: Any) -> None:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    def _service_name(self) -> str:
        return str(getattr(self.settings, "service", "") or "app").strip()

    def _compose_dir_value(self) -> str:
        return str(getattr(self.settings, "compose_dir", "") or "").strip()

    def _compose_dir(self) -> Path:
        return Path(self._compose_dir_value())

    def _compose_file(self) -> str:
        return str(getattr(self.settings, "compose_file", "") or "docker-compose.yml").strip()

    def _helper_image(self) -> str:
        return str(getattr(self.settings, "helper_image", "") or "docker:28-cli").strip()

    def _host_compose_dir_value(self) -> str:
        return str(getattr(self.settings, "host_compose_dir", "") or "").strip()

    def _host_data_dir_value(self) -> str:
        return str(getattr(self.settings, "host_data_dir", "") or "").strip()

    def _helper_compose_project_dir(self) -> Path:
        return Path(self._host_compose_dir_value())

    def _helper_job_source_dir(self, job_id: str) -> Path:
        return Path(self._host_data_dir_value()) / "update-jobs" / job_id

    @staticmethod
    def _valid_compose_file(value: str) -> bool:
        if not value or not DOCKER_COMPOSE_FILE_RE.match(value):
            return False
        path = PurePosixPath(value)
        return not path.is_absolute() and ".." not in path.parts and str(path) not in {"", "."}

    @staticmethod
    def _helper_script(
        *,
        service: str,
        compose_file: str,
        project_dir: Path,
        target_tag: str,
        target_version: str,
        release_url: str,
    ) -> str:
        target_tag_json = json.dumps(str(target_tag), ensure_ascii=True)
        target_version_json = json.dumps(str(target_version), ensure_ascii=True)
        release_url_json = json.dumps(str(release_url), ensure_ascii=True)
        compose_path = shlex.quote(str(project_dir / compose_file))
        service_arg = shlex.quote(service)
        return f"""#!/bin/sh
set -u

write_status() {{
  state="$1"
  message="$2"
  finished="$3"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$finished" = "1" ]; then
    cat > /job/status.json.tmp <<JSON
{{"status":"$state","message":"$message","target_tag":{target_tag_json},"target_version":{target_version_json},"release_url":{release_url_json},"updated_at":"$now","finished_at":"$now"}}
JSON
  else
    cat > /job/status.json.tmp <<JSON
{{"status":"$state","message":"$message","target_tag":{target_tag_json},"target_version":{target_version_json},"release_url":{release_url_json},"updated_at":"$now"}}
JSON
  fi
  mv /job/status.json.tmp /job/status.json
}}

log() {{
  printf '%s %s\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" | tee -a /job/log.txt
}}

write_status running "Docker compose update started" 0
log "Pulling latest Docker image for {service}"
if ! docker compose -f {compose_path} pull {service_arg} >> /job/log.txt 2>&1; then
  write_status failed "Docker compose pull failed" 1
  exit 1
fi

log "Recreating Docker service {service}"
if ! docker compose -f {compose_path} up -d {service_arg} >> /job/log.txt 2>&1; then
  write_status failed "Docker compose up failed" 1
  exit 1
fi

write_status succeeded "Docker update completed" 1
"""


def _checksum_for_asset(asset_name: str, checksum_text: str) -> str | None:
    asset_basename = Path(asset_name).name
    for raw_line in checksum_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        sha_format = re.match(r"^SHA256\((?P<name>.+)\)\s*=\s*(?P<digest>[a-fA-F0-9]{64})$", line)
        if sha_format and Path(sha_format.group("name")).name == asset_basename:
            return sha_format.group("digest")

        parts = line.split()
        if len(parts) < 2 or not CHECKSUM_RE.match(parts[0]):
            continue
        digest = parts[0]
        filename = parts[-1].lstrip("*")
        if filename == asset_name or Path(filename).name == asset_basename:
            return digest
    return None


def _validate_tar_path(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Archive contains unsafe path: {name}")


def _find_release_payload_root(members: list[tarfile.TarInfo]) -> PurePosixPath:
    member_by_path = {PurePosixPath(member.name): member for member in members}
    for member in members:
        member_path = PurePosixPath(member.name)
        if member_path.name not in BINARY_NAMES or not member.isfile():
            continue
        root = member_path.parent
        version_path = root / "VERSION"
        web_dist_path = root / "web_dist"
        version_member = member_by_path.get(version_path)
        web_dist_member = member_by_path.get(web_dist_path)
        if version_member is not None and version_member.isfile() and web_dist_member is not None and web_dist_member.isdir():
            return root
    raise ValueError("Release asset archive does not contain genapi, VERSION, and web_dist.")


def _relative_to_payload_root(path: PurePosixPath, root: PurePosixPath) -> PurePosixPath | None:
    if str(root) == ".":
        return path
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _validate_release_payload_tree(payload_dir: Path) -> None:
    executable = payload_dir / "genapi"
    if not executable.is_file():
        raise ValueError("Release asset archive does not contain a genapi binary.")
    if not (payload_dir / "VERSION").is_file():
        raise ValueError("Release asset archive does not contain VERSION.")
    if not (payload_dir / "web_dist").is_dir():
        raise ValueError("Release asset archive does not contain web_dist.")


def _safe_release_dir_prefix(version: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(version or "").strip().lstrip("v"))
    value = value.strip(".-")
    return f"release-{value or 'unknown'}"


def _safe_container_suffix(value: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    suffix = suffix.strip(".-")
    return suffix[:48] or "job"


def _docker_container_id(container: Any) -> str:
    if isinstance(container, dict):
        return str(container.get("id") or container.get("short_id") or "")
    return str(getattr(container, "id", "") or getattr(container, "short_id", "") or "")


DockerUpdateExecutor = DockerComposeUpdateExecutor
