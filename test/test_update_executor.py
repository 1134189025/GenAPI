from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class FakeContainerManager:
    def __init__(self):
        self.run_calls: list[dict[str, object]] = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        return {"id": "helper-container"}


class FakeDockerClient:
    def __init__(self):
        self.containers = FakeContainerManager()


class UpdateExecutorTests(unittest.TestCase):
    def test_start_helper_container_uses_docker_socket_compose_dir_and_safe_env(self) -> None:
        from services.update_executor import DockerUpdateExecutor
        from services.update_service import UpdateSettings

        docker_client = FakeDockerClient()
        settings = UpdateSettings(
            enabled=True,
            repo="owner/project",
            service="app",
            compose_dir="/srv/genapi",
            helper_image="docker:28-cli",
            timeout_seconds=123,
            health_url="http://host.docker.internal:3000/version",
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            executor = DockerUpdateExecutor(
                data_dir=Path(tmp_dir),
                settings=settings,
                docker_client_factory=lambda: docker_client,
            )
            executor.start(
                job_id="job-123",
                target_tag="v0.1.4",
                target_version="0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
            )

        self.assertEqual(len(docker_client.containers.run_calls), 1)
        call = docker_client.containers.run_calls[0]
        self.assertEqual(call["image"], "docker:28-cli")
        self.assertEqual(call["name"], "genapi-update-job-123")
        self.assertTrue(call["detach"])
        self.assertEqual(call["working_dir"], "/srv/genapi")
        self.assertEqual(call["network_mode"], "bridge")
        self.assertEqual(call["extra_hosts"], {"host.docker.internal": "host-gateway"})
        self.assertEqual(call["volumes"]["/var/run/docker.sock"]["bind"], "/var/run/docker.sock")
        self.assertEqual(call["volumes"]["/srv/genapi"]["bind"], "/srv/genapi")
        self.assertIn("GENAPI_UPDATE_SERVICE=app", call["environment"])
        self.assertIn("GENAPI_UPDATE_HEALTH_URL=http://host.docker.internal:3000/version", call["environment"])
        self.assertIn("GENAPI_UPDATE_COMPOSE_DIR=/srv/genapi", call["environment"])
        self.assertIn("GENAPI_UPDATE_TARGET_TAG=v0.1.4", call["environment"])
        self.assertIn("genapi.update.job_id=job-123", call["labels"].values())
        script = str(call["command"])
        self.assertIn('COMPOSE_DIR="${GENAPI_UPDATE_COMPOSE_DIR:?missing compose dir}"', script)
        self.assertIn('cd "${COMPOSE_DIR}"', script)
        self.assertIn("--exclude=data/update-backups", script)
        self.assertIn("health_body", script)
        self.assertIn('grep -F "${TARGET_VERSION}"', script)
        self.assertNotIn("cd /workspace", script)

    def test_preflight_fails_closed_when_updater_disabled_or_compose_dir_missing(self) -> None:
        from services.update_executor import DockerUpdateExecutor
        from services.update_service import UpdateSettings

        disabled = DockerUpdateExecutor(
            data_dir=Path("/tmp"),
            settings=UpdateSettings(enabled=False, repo="owner/project", compose_dir="/srv/genapi"),
            docker_client_factory=lambda: FakeDockerClient(),
        )
        self.assertIn("disabled", disabled.preflight()["errors"][0])

        missing_compose = DockerUpdateExecutor(
            data_dir=Path("/tmp"),
            settings=UpdateSettings(enabled=True, repo="owner/project", compose_dir=""),
            docker_client_factory=lambda: FakeDockerClient(),
        )
        self.assertIn("GENAPI_UPDATE_COMPOSE_DIR", missing_compose.preflight()["errors"][0])

        missing_health = DockerUpdateExecutor(
            data_dir=Path("/tmp"),
            settings=UpdateSettings(enabled=True, repo="owner/project", compose_dir="/srv/genapi", health_url=""),
            docker_client_factory=lambda: FakeDockerClient(),
        )
        self.assertIn("GENAPI_UPDATE_HEALTH_URL", missing_health.preflight()["errors"][0])


if __name__ == "__main__":
    unittest.main()
