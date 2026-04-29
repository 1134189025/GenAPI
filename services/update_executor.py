from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


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

        if not str(getattr(self.settings, "health_url", "") or "").strip():
            errors.append("GENAPI_UPDATE_HEALTH_URL must be set so the updater can verify the app and roll back on failure.")

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
    def _build_helper_command() -> list[str]:
        script = r"""#!/bin/sh
set -eu

JOB_ID="${GENAPI_UPDATE_JOB_ID:?missing job id}"
SERVICE="${GENAPI_UPDATE_SERVICE:?missing service}"
TARGET_TAG="${GENAPI_UPDATE_TARGET_TAG:?missing target tag}"
TARGET_VERSION="${GENAPI_UPDATE_TARGET_VERSION:?missing target version}"
RELEASE_URL="${GENAPI_UPDATE_RELEASE_URL:-}"
TIMEOUT_SECONDS="${GENAPI_UPDATE_TIMEOUT_SECONDS:-600}"
HEALTH_URL="${GENAPI_UPDATE_HEALTH_URL:-}"
COMPOSE_DIR="${GENAPI_UPDATE_COMPOSE_DIR:?missing compose dir}"

JOB_DIR="${COMPOSE_DIR}/data/update-jobs/${JOB_ID}"
LOG_FILE="${JOB_DIR}/log.txt"
STATUS_FILE="${JOB_DIR}/status.json"
BACKUP_DIR="${COMPOSE_DIR}/data/update-backups"

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
  cat >"${STATUS_FILE}" <<EOF
{"status": "$(json_escape "${status}")", "message": "$(json_escape "${message}")", "target_tag": "$(json_escape "${TARGET_TAG}")", "target_version": "$(json_escape "${TARGET_VERSION}")", "release_url": "$(json_escape "${RELEASE_URL}")", "updated_at": "$(now_utc)"${finished_json}}
EOF
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
    health_body="$(wget -q -T 5 -O - "${HEALTH_URL}" 2>/dev/null || true)"
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
  docker compose -f docker-compose.yml -f "${override}" up -d "${SERVICE}"
  rm -f "${override}"
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
rollback_image_ref=""
if [ -n "${previous_container}" ]; then
  previous_image="$(docker inspect --format '{{.Image}}' "${previous_container}" 2>/dev/null || true)"
  if [ -n "${previous_image}" ]; then
    rollback_image_ref="genapi-rollback-${JOB_ID}:previous"
    docker tag "${previous_image}" "${rollback_image_ref}" || rollback_image_ref=""
  fi
fi

if [ -d "${COMPOSE_DIR}/data" ]; then
  backup="${BACKUP_DIR}/${JOB_ID}-data.tar.gz"
  echo "Creating data backup ${backup}"
  tar --exclude=data/update-backups -czf "${backup}" -C "${COMPOSE_DIR}" data
fi

echo "Pulling service ${SERVICE}"
compose pull "${SERVICE}" || finish_failed "Failed to pull updated image for ${SERVICE}"

echo "Starting service ${SERVICE}"
compose up -d "${SERVICE}" || finish_failed "Failed to start ${SERVICE}"

if ! health_check; then
  echo "Health check failed for ${HEALTH_URL}"
  if rollback_image "${rollback_image_ref}"; then
    finish_failed "Health check failed; rolled back container image only. Data was not restored automatically."
  fi
  finish_failed "Health check failed and image-only rollback could not be completed. Data was not restored automatically."
fi

write_status "succeeded" "Update completed successfully" "$(now_utc)"
echo "Update completed successfully"
"""
        return ["sh", "-c", script]
