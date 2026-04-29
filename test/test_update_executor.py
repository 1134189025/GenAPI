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
        self.assertIn('BACKUP_DIR="${COMPOSE_DIR}/.genapi-update-backups"', script)
        self.assertIn("health_body", script)
        self.assertIn('grep -F "${TARGET_VERSION}"', script)
        self.assertIn('TARGET_IMAGE="${GENAPI_UPDATE_TARGET_IMAGE:-}"', script)
        self.assertIn("prepare_target_image_override", script)
        self.assertIn("start_prepared_target_image_override", script)
        self.assertIn("restore_data_backup", script)
        self.assertIn("restore_copy=", script)
        self.assertIn("tar -xzf", script)
        self.assertIn('restore_data_backup "${backup}"', script)
        self.assertIn('rollback_image "${rollback_image_ref}"', script)
        self.assertIn('compose stop "${SERVICE}"', script)
        self.assertIn('finish_failed "Failed to create data backup before updating ${SERVICE}"', script)
        self.assertIn("restart_service_after_backup_failure", script)
        pull_index = script.index('prepare_target_image_override "${TARGET_IMAGE}"')
        backup_stop_index = script.index('echo "Stopping service ${SERVICE} to create a consistent data backup"')
        backup_tar_index = script.index('tar -czf "${backup}"')
        backup_restart_index = script.rindex("restart_service_after_backup_failure")
        backup_fail_index = script.index('finish_failed "Failed to create data backup before updating ${SERVICE}"')
        update_start_index = script.index('start_prepared_target_image_override "${target_override}"')
        self.assertLess(pull_index, backup_stop_index)
        self.assertLess(backup_stop_index, backup_tar_index)
        self.assertLess(backup_tar_index, backup_restart_index)
        self.assertLess(backup_restart_index, backup_fail_index)
        self.assertLess(backup_tar_index, update_start_index)
        failure_stop_index = script.index('echo "Stopping service ${SERVICE} before failed-update recovery"')
        failure_restore_index = script.index('restore_data_backup "${backup}"')
        self.assertLess(failure_stop_index, failure_restore_index)
        self.assertLess(script.index('restore_data_backup "${backup}"'), script.rindex('rollback_image "${rollback_image_ref}"'))
        self.assertNotIn("rolled back container image only", script)
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
        self.assertTrue(any("GENAPI_UPDATE_HEALTH_URL" in error for error in missing_health.preflight()["errors"]))

    def test_preflight_validates_local_compose_inputs_before_starting_helper(self) -> None:
        from services.update_executor import DockerUpdateExecutor
        from services.update_service import UpdateSettings

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            missing_dir = DockerUpdateExecutor(
                data_dir=base / "data",
                settings=UpdateSettings(
                    enabled=True,
                    repo="owner/project",
                    service="app",
                    compose_dir=str(base / "missing"),
                    helper_image="docker:28-cli",
                    timeout_seconds=123,
                    health_url="http://host.docker.internal:3000/version",
                ),
                docker_client_factory=lambda: FakeDockerClient(),
            ).preflight()
            self.assertTrue(missing_dir["ok"])
            self.assertTrue(any("not visible" in warning for warning in missing_dir["warnings"]))

            compose_dir = base / "compose"
            compose_dir.mkdir()
            missing_file = DockerUpdateExecutor(
                data_dir=base / "data",
                settings=UpdateSettings(
                    enabled=True,
                    repo="owner/project",
                    service="app",
                    compose_dir=str(compose_dir),
                    helper_image="docker:28-cli",
                    timeout_seconds=123,
                    health_url="http://host.docker.internal:3000/version",
                ),
                docker_client_factory=lambda: FakeDockerClient(),
            ).preflight()
            self.assertFalse(missing_file["ok"])
            self.assertTrue(any("docker-compose.yml" in error for error in missing_file["errors"]))

            (compose_dir / "docker-compose.yml").write_text("services:\n  app:\n    image: genapi:test\n", encoding="utf-8")
            invalid_values = DockerUpdateExecutor(
                data_dir=base / "data",
                settings=UpdateSettings(
                    enabled=True,
                    repo="owner/project",
                    service="",
                    compose_dir=str(compose_dir),
                    helper_image="",
                    timeout_seconds=0,
                    health_url="ftp://host/version",
                ),
                docker_client_factory=lambda: FakeDockerClient(),
            ).preflight()

        self.assertFalse(invalid_values["ok"])
        self.assertTrue(any("GENAPI_UPDATE_SERVICE" in error for error in invalid_values["errors"]))
        self.assertTrue(any("GENAPI_UPDATE_HELPER_IMAGE" in error for error in invalid_values["errors"]))
        self.assertTrue(any("GENAPI_UPDATE_TIMEOUT_SECONDS" in error for error in invalid_values["errors"]))
        self.assertTrue(any("GENAPI_UPDATE_HEALTH_URL" in error for error in invalid_values["errors"]))

    def test_start_helper_script_keeps_backups_outside_live_data_and_validates_before_restore(self) -> None:
        from services.update_executor import DockerUpdateExecutor

        script = str(DockerUpdateExecutor._build_helper_command())

        self.assertIn('BACKUP_DIR="${COMPOSE_DIR}/.genapi-update-backups"', script)
        self.assertNotIn('BACKUP_DIR="${COMPOSE_DIR}/data/update-backups"', script)
        validate_index = script.index('tar -tzf "${backup_file}"')
        remove_index = script.index('rm -rf "${COMPOSE_DIR}/data"')
        extract_index = script.index('tar -xzf "${restore_copy}" -C "${COMPOSE_DIR}"')
        self.assertLess(validate_index, remove_index)
        self.assertLess(remove_index, extract_index)

    def test_preflight_reports_malformed_health_url_without_throwing(self) -> None:
        from services.update_executor import DockerUpdateExecutor
        from services.update_service import UpdateSettings

        with tempfile.TemporaryDirectory() as tmp_dir:
            compose_dir = Path(tmp_dir) / "compose"
            compose_dir.mkdir()
            (compose_dir / "docker-compose.yml").write_text(
                "services:\n  app:\n    image: genapi:test\n",
                encoding="utf-8",
            )
            result = DockerUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(
                    enabled=True,
                    repo="owner/project",
                    service="app",
                    compose_dir=str(compose_dir),
                    helper_image="docker:28-cli",
                    timeout_seconds=123,
                    health_url="http://[::1",
                ),
                docker_client_factory=lambda: FakeDockerClient(),
            ).preflight()

        self.assertFalse(result["ok"])
        self.assertTrue(any("GENAPI_UPDATE_HEALTH_URL" in error for error in result["errors"]))

    def test_preflight_rejects_loopback_health_url_for_bridge_helper(self) -> None:
        from services.update_executor import DockerUpdateExecutor
        from services.update_service import UpdateSettings

        with tempfile.TemporaryDirectory() as tmp_dir:
            compose_dir = Path(tmp_dir) / "compose"
            compose_dir.mkdir()
            (compose_dir / "docker-compose.yml").write_text(
                "services:\n  app:\n    image: genapi:test\n",
                encoding="utf-8",
            )
            result = DockerUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(
                    enabled=True,
                    repo="owner/project",
                    service="app",
                    compose_dir=str(compose_dir),
                    helper_image="docker:28-cli",
                    timeout_seconds=123,
                    health_url="http://127.0.0.1:3000/version",
                ),
                docker_client_factory=lambda: FakeDockerClient(),
            ).preflight()

        self.assertFalse(result["ok"])
        self.assertTrue(any("loopback" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
