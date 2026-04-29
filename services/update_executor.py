from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


DockerClientFactory = Callable[[], Any]


class DockerUpdateExecutor:
    def __init__(
        self,
        data_dir: Path | str,
        settings: Any,
        docker_client_factory: DockerClientFactory | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings
        self._docker_client_factory = docker_client_factory

    def preflight(self) -> dict[str, object]:
        errors: list[str] = []
        warnings: list[str] = []

        if not bool(getattr(self.settings, "enabled", False)):
            errors.append("Web updater is disabled. Set GENAPI_ENABLE_WEB_UPDATER=true to enable Docker updates.")
            return {"ok": False, "errors": errors, "warnings": warnings}

        compose_dir = str(getattr(self.settings, "compose_dir", "") or "")
        if not compose_dir:
            errors.append("GENAPI_UPDATE_COMPOSE_DIR must be set to the host directory containing docker-compose.yml.")
        elif not Path(compose_dir).is_absolute():
            errors.append("GENAPI_UPDATE_COMPOSE_DIR must be an absolute host path.")
        else:
            compose_path = Path(compose_dir)
            if not compose_path.exists():
                warnings.append(
                    "GENAPI_UPDATE_COMPOSE_DIR is not visible inside this container; "
                    "the Docker daemon will validate the host path when the helper starts."
                )
            elif not compose_path.is_dir():
                errors.append("GENAPI_UPDATE_COMPOSE_DIR must point to a host directory.")
            elif not (compose_path / "docker-compose.yml").is_file():
                errors.append("GENAPI_UPDATE_COMPOSE_DIR must contain docker-compose.yml.")

        service = str(getattr(self.settings, "service", "") or "").strip()
        if not service:
            errors.append("GENAPI_UPDATE_SERVICE must be set to the Compose service that should be updated.")

        helper_image = str(getattr(self.settings, "helper_image", "") or "").strip()
        if not helper_image:
            errors.append("GENAPI_UPDATE_HELPER_IMAGE must be set to a Docker image containing docker compose.")

        try:
            timeout_seconds = int(getattr(self.settings, "timeout_seconds", 0) or 0)
        except (TypeError, ValueError):
            timeout_seconds = 0
        if timeout_seconds <= 0:
            errors.append("GENAPI_UPDATE_TIMEOUT_SECONDS must be a positive integer.")

        health_url = str(getattr(self.settings, "health_url", "") or "").strip()
        if not health_url:
            errors.append("GENAPI_UPDATE_HEALTH_URL must be set so the updater can verify the app and roll back on failure.")
        else:
            try:
                parsed_health_url = urlparse(health_url)
            except ValueError:
                parsed_health_url = None
            if parsed_health_url is not None:
                try:
                    health_hostname = parsed_health_url.hostname
                    parsed_health_url.port
                except ValueError:
                    health_hostname = None
            if (
                parsed_health_url is None
                or parsed_health_url.scheme not in {"http", "https"}
                or not parsed_health_url.netloc
                or not health_hostname
            ):
                errors.append("GENAPI_UPDATE_HEALTH_URL must be an http or https URL.")
            elif self._is_loopback_hostname(health_hostname):
                errors.append(
                    "GENAPI_UPDATE_HEALTH_URL must not use a localhost or loopback host because the "
                    "bridge helper container cannot reach the app through its own loopback; use "
                    "http://host.docker.internal:PORT/version."
                )

        if self._docker_client_factory is None:
            if not Path("/var/run/docker.sock").exists():
                errors.append("Docker socket /var/run/docker.sock is not available to this process.")
            try:
                import docker  # noqa: F401
            except ImportError:
                errors.append("Docker SDK is not installed. Install it with: uv add docker or pip install docker.")

        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def start(self, job_id: str, target_tag: str, target_version: str, release_url: str) -> Any:
        client = self._get_docker_client()
        compose_dir = str(getattr(self.settings, "compose_dir", "") or "")
        command = self._build_helper_command()

        return client.containers.run(
            image=str(getattr(self.settings, "helper_image", "docker:28-cli")),
            name=f"genapi-update-{job_id}",
            command=command,
            detach=True,
            working_dir=compose_dir,
            network_mode="bridge",
            extra_hosts={"host.docker.internal": "host-gateway"},
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                compose_dir: {"bind": compose_dir, "mode": "rw"},
            },
            environment=[
                f"GENAPI_UPDATE_JOB_ID={job_id}",
                f"GENAPI_UPDATE_SERVICE={getattr(self.settings, 'service', 'app')}",
                f"GENAPI_UPDATE_COMPOSE_DIR={compose_dir}",
                f"GENAPI_UPDATE_TARGET_TAG={target_tag}",
                f"GENAPI_UPDATE_TARGET_VERSION={target_version}",
                f"GENAPI_UPDATE_TARGET_IMAGE={getattr(self.settings, 'target_image', '') or ''}",
                f"GENAPI_UPDATE_RELEASE_URL={release_url}",
                f"GENAPI_UPDATE_TIMEOUT_SECONDS={getattr(self.settings, 'timeout_seconds', 600)}",
                f"GENAPI_UPDATE_HEALTH_URL={getattr(self.settings, 'health_url', '') or ''}",
            ],
            labels={
                "genapi.update.executor": "docker-helper",
                "genapi.update.job_id": job_id,
                "genapi.update.job": f"genapi.update.job_id={job_id}",
            },
        )

    def _get_docker_client(self) -> Any:
        if self._docker_client_factory is not None:
            return self._docker_client_factory()

        try:
            import docker
        except ImportError as exc:
            raise RuntimeError("Docker SDK is not installed. Install it with: uv add docker or pip install docker.") from exc

        return docker.from_env()

    @staticmethod
    def _is_loopback_hostname(hostname: str) -> bool:
        normalized = str(hostname or "").strip().rstrip(".").lower()
        if normalized == "localhost":
            return True
        try:
            return ip_address(normalized).is_loopback
        except ValueError:
            return False

    @staticmethod
    def _build_helper_command() -> list[str]:
        script = r"""#!/bin/sh
set -eu

JOB_ID="${GENAPI_UPDATE_JOB_ID:?missing job id}"
SERVICE="${GENAPI_UPDATE_SERVICE:?missing service}"
TARGET_TAG="${GENAPI_UPDATE_TARGET_TAG:?missing target tag}"
TARGET_VERSION="${GENAPI_UPDATE_TARGET_VERSION:?missing target version}"
TARGET_IMAGE="${GENAPI_UPDATE_TARGET_IMAGE:-}"
RELEASE_URL="${GENAPI_UPDATE_RELEASE_URL:-}"
TIMEOUT_SECONDS="${GENAPI_UPDATE_TIMEOUT_SECONDS:-600}"
HEALTH_URL="${GENAPI_UPDATE_HEALTH_URL:-}"
COMPOSE_DIR="${GENAPI_UPDATE_COMPOSE_DIR:?missing compose dir}"

JOB_DIR="${COMPOSE_DIR}/data/update-jobs/${JOB_ID}"
LOG_FILE="${JOB_DIR}/log.txt"
STATUS_FILE="${JOB_DIR}/status.json"
BACKUP_DIR="${COMPOSE_DIR}/.genapi-update-backups"

mkdir -p "${JOB_DIR}" "${BACKUP_DIR}"
touch "${LOG_FILE}"
exec >>"${LOG_FILE}" 2>&1

now_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

json_escape() {
  printf "%s" "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

write_status() {
  status="$1"
  message="$2"
  finished="${3:-}"
  if [ -n "${finished}" ]; then
    finished_json=", \"finished_at\": \"$(json_escape "${finished}")\""
  else
    finished_json=""
  fi
  status_tmp="${STATUS_FILE}.tmp.$$"
  cat >"${status_tmp}" <<EOF
{"status": "$(json_escape "${status}")", "message": "$(json_escape "${message}")", "target_tag": "$(json_escape "${TARGET_TAG}")", "target_version": "$(json_escape "${TARGET_VERSION}")", "release_url": "$(json_escape "${RELEASE_URL}")", "updated_at": "$(now_utc)"${finished_json}}
EOF
  mv "${status_tmp}" "${STATUS_FILE}"
}

finish_failed() {
  write_status "failed" "$1" "$(now_utc)"
  exit 1
}

compose() {
  docker compose "$@"
}

health_check() {
  [ -n "${HEALTH_URL}" ] || finish_failed "GENAPI_UPDATE_HEALTH_URL is required for safe updates"
  deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while [ "$(date +%s)" -le "${deadline}" ]; do
    if ! health_body="$(wget -q -T 5 -O - "${HEALTH_URL}" 2>/dev/null)"; then
      health_body=""
    fi
    if [ -n "${health_body}" ]; then
      case "${HEALTH_URL}" in
        */version)
          if printf "%s" "${health_body}" | grep -F "${TARGET_VERSION}" >/dev/null 2>&1; then
            return 0
          fi
          ;;
        *)
          return 0
          ;;
      esac
    fi
    sleep 5
  done
  return 1
}

rollback_image() {
  previous_image="$1"
  [ -n "${previous_image}" ] || return 1

  override="$(mktemp /tmp/genapi-rollback.XXXXXX.yml)"
  cat >"${override}" <<EOF
services:
  ${SERVICE}:
    image: ${previous_image}
EOF
  if ! docker compose -f docker-compose.yml -f "${override}" up -d "${SERVICE}"; then
    rm -f "${override}"
    return 1
  fi
  rm -f "${override}"
}

target_image_from_ref() {
  image_ref="$1"
  [ -n "${image_ref}" ] || return 1
  case "${image_ref}" in
    *@*)
      printf "%s:%s" "${image_ref%%@*}" "${TARGET_TAG}"
      ;;
    *:*)
      prefix="${image_ref%:*}"
      suffix="${image_ref##*:}"
      case "${suffix}" in
        */*)
          printf "%s:%s" "${image_ref}" "${TARGET_TAG}"
          ;;
        *)
          printf "%s:%s" "${prefix}" "${TARGET_TAG}"
          ;;
      esac
      ;;
    *)
      printf "%s:%s" "${image_ref}" "${TARGET_TAG}"
      ;;
  esac
}

prepare_target_image_override() {
  target_image="$1"
  [ -n "${target_image}" ] || return 1

  target_override="$(mktemp /tmp/genapi-target-image.XXXXXX.yml)"
  cat >"${target_override}" <<EOF
services:
  ${SERVICE}:
    image: ${target_image}
EOF
  if ! docker compose -f docker-compose.yml -f "${target_override}" pull "${SERVICE}"; then
    rm -f "${target_override}"
    target_override=""
    return 1
  fi
}

start_prepared_target_image_override() {
  override="$1"
  [ -n "${override}" ] || return 1

  if ! docker compose -f docker-compose.yml -f "${override}" up -d "${SERVICE}"; then
    rm -f "${override}"
    return 1
  fi
  rm -f "${override}"
}

restore_data_backup() {
  backup_file="$1"
  [ -n "${backup_file}" ] || return 1
  [ -f "${backup_file}" ] || return 1

  echo "Restoring data backup ${backup_file}"
  restore_copy="$(mktemp /tmp/genapi-data-backup.XXXXXX.tar.gz)"
  if ! cp "${backup_file}" "${restore_copy}"; then
    rm -f "${restore_copy}"
    return 1
  fi
  if ! tar -tzf "${backup_file}" >/dev/null 2>&1; then
    rm -f "${restore_copy}"
    return 1
  fi
  rm -rf "${COMPOSE_DIR}/data"
  if ! tar -xzf "${restore_copy}" -C "${COMPOSE_DIR}"; then
    rm -f "${restore_copy}"
    return 1
  fi
  rm -f "${restore_copy}"
}

restart_service_after_backup_failure() {
  echo "Restarting service ${SERVICE} after data backup failure"
  if [ -n "${rollback_image_ref}" ]; then
    rollback_image "${rollback_image_ref}" || compose up -d "${SERVICE}" || true
    return 0
  fi
  compose up -d "${SERVICE}" || true
}

echo "Starting Genapi update job ${JOB_ID}"
write_status "running" "Starting Docker helper update"

cd "${COMPOSE_DIR}"
[ -f docker-compose.yml ] || finish_failed "docker-compose.yml was not found in compose directory"
docker version >/dev/null 2>&1 || finish_failed "Docker daemon is not reachable through /var/run/docker.sock"
docker compose version >/dev/null 2>&1 || finish_failed "docker compose is not available in the helper image"
compose config >/dev/null || finish_failed "docker-compose.yml failed validation"

previous_container="$(compose ps -q "${SERVICE}" 2>/dev/null || true)"
previous_image=""
previous_image_ref=""
rollback_image_ref=""
if [ -n "${previous_container}" ]; then
  previous_image="$(docker inspect --format '{{.Image}}' "${previous_container}" 2>/dev/null || true)"
  previous_image_ref="$(docker inspect --format '{{.Config.Image}}' "${previous_container}" 2>/dev/null || true)"
  if [ -n "${previous_image}" ]; then
    rollback_image_ref="genapi-rollback-${JOB_ID}:previous"
    docker tag "${previous_image}" "${rollback_image_ref}" || rollback_image_ref=""
  fi
fi

if [ -z "${TARGET_IMAGE}" ]; then
  TARGET_IMAGE="$(target_image_from_ref "${previous_image_ref}" || true)"
fi

target_override=""
if [ -n "${TARGET_IMAGE}" ]; then
  echo "Pulling service ${SERVICE} image ${TARGET_IMAGE}"
  prepare_target_image_override "${TARGET_IMAGE}" || finish_failed "Failed to pull ${SERVICE} image ${TARGET_IMAGE}"
else
  echo "Pulling service ${SERVICE}"
  compose pull "${SERVICE}" || finish_failed "Failed to pull updated image for ${SERVICE}"
fi

backup=""
if [ -d "${COMPOSE_DIR}/data" ]; then
  backup="${BACKUP_DIR}/${JOB_ID}-data.tar.gz"
  echo "Stopping service ${SERVICE} to create a consistent data backup"
  compose stop "${SERVICE}" || finish_failed "Failed to stop ${SERVICE} before creating data backup"
  echo "Creating data backup ${backup}"
  if ! tar -czf "${backup}" -C "${COMPOSE_DIR}" data; then
    restart_service_after_backup_failure
    finish_failed "Failed to create data backup before updating ${SERVICE}"
  fi
fi

if [ -n "${target_override}" ]; then
  echo "Starting service ${SERVICE} with image ${TARGET_IMAGE}"
  start_prepared_target_image_override "${target_override}" || finish_failed "Failed to start ${SERVICE} with ${TARGET_IMAGE}"
else
  echo "Starting service ${SERVICE}"
  compose up -d "${SERVICE}" || finish_failed "Failed to start ${SERVICE}"
fi

if ! health_check; then
  echo "Health check failed for ${HEALTH_URL}"
  echo "Stopping service ${SERVICE} before failed-update recovery"
  compose stop "${SERVICE}" || true
  restore_ok=1
  if [ -n "${backup}" ]; then
    restore_ok=0
    if restore_data_backup "${backup}"; then
      restore_ok=1
    fi
  fi
  rollback_ok=0
  if rollback_image "${rollback_image_ref}"; then
    rollback_ok=1
  fi
  if [ "${rollback_ok}" = "1" ] && [ "${restore_ok}" = "1" ]; then
    write_status "failed" "Health check failed; restored previous image and data backup." "$(now_utc)"
    exit 1
  fi
  if [ "${rollback_ok}" = "1" ]; then
    finish_failed "Health check failed; restored previous image but data restore failed. Manual recovery may be required."
  fi
  if [ "${restore_ok}" = "1" ]; then
    finish_failed "Health check failed; restored data backup but image rollback failed. Manual recovery may be required."
  fi
  finish_failed "Health check failed; image rollback and data restore failed. Manual recovery is required."
fi

write_status "succeeded" "Update completed successfully" "$(now_utc)"
echo "Update completed successfully"
"""
        return ["sh", "-c", script]
