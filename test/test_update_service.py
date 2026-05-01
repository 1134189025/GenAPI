from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object], status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class UpdateServiceTests(unittest.TestCase):
    def test_load_update_settings_reads_new_environment_and_explicit_mode(self) -> None:
        from services.update_service import load_update_settings

        with patch.dict(
            os.environ,
            {
                "GENAPI_UPDATE_REPO": "owner/project",
                "GITHUB_TOKEN": "ghs_secret",
                "GENAPI_UPDATE_TIMEOUT_SECONDS": "123",
                "GENAPI_DEPLOYMENT_MODE": "systemd-binary",
                "GENAPI_BUILD_TYPE": "release",
                "GENAPI_UPDATE_MAX_DOWNLOAD_BYTES": "4096",
            },
            clear=True,
        ):
            settings = load_update_settings()

        self.assertEqual(settings.repo, "owner/project")
        self.assertEqual(settings.github_token, "ghs_secret")
        self.assertEqual(settings.timeout_seconds, 123)
        self.assertEqual(settings.deployment_mode, "systemd-binary")
        self.assertEqual(settings.build_type, "release")
        self.assertEqual(settings.max_download_bytes, 4096)

    def test_load_update_settings_auto_detects_frozen_binary_before_docker(self) -> None:
        from services.update_service import load_update_settings

        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "frozen", True, create=True):
            settings = load_update_settings()

        self.assertEqual(settings.repo, "1134189025/GenAPI")
        self.assertEqual(settings.timeout_seconds, 900)
        self.assertEqual(settings.deployment_mode, "systemd-binary")
        self.assertEqual(settings.build_type, "source")
        self.assertEqual(settings.max_download_bytes, 524288000)

    def test_release_status_detects_newer_release_and_compatible_asset(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

        marker = _runtime_marker()

        def opener(request, timeout):
            self.assertEqual(request.full_url, "https://api.github.com/repos/owner/project/releases/latest")
            self.assertEqual(request.headers.get("Accept"), "application/vnd.github+json")
            return FakeHTTPResponse(
                {
                    "tag_name": "v0.1.4",
                    "name": "Genapi 0.1.4",
                    "html_url": "https://github.com/owner/project/releases/tag/v0.1.4",
                    "published_at": "2026-04-29T12:00:00Z",
                    "body": "Release notes",
                    "prerelease": False,
                    "draft": False,
                    "assets": [
                        {
                            "name": f"genapi_v0.1.4_{marker}.tar.gz",
                            "browser_download_url": f"https://github.com/owner/project/releases/download/v0.1.4/genapi_v0.1.4_{marker}.tar.gz",
                            "size": 1024,
                        },
                        {
                            "name": "checksums.txt",
                            "browser_download_url": "https://github.com/owner/project/releases/download/v0.1.4/checksums.txt",
                            "size": 128,
                        },
                    ],
                }
            )

        status = build_release_status(
            current_version="0.1.3",
            settings=UpdateSettings(repo="owner/project", deployment_mode="systemd-binary", build_type="release"),
            opener=opener,
            force=True,
        )

        self.assertTrue(status["update_available"])
        self.assertTrue(status["has_update"])
        self.assertTrue(status["can_update"])
        self.assertTrue(status["enabled"])
        self.assertEqual(status["mode"], "systemd-binary")
        self.assertEqual(status["deployment_mode"], "systemd-binary")
        self.assertEqual(status["build_type"], "release")
        self.assertEqual(status["current_version"], "0.1.3")
        self.assertEqual(status["latest_version"], "0.1.4")
        self.assertEqual(status["latest_tag"], "v0.1.4")
        self.assertEqual(status["release_url"], "https://github.com/owner/project/releases/tag/v0.1.4")
        self.assertEqual(status["release_info"]["assets"][0]["name"], f"genapi_v0.1.4_{marker}.tar.gz")
        self.assertEqual(status["repo"], "owner/project")

    def test_release_status_requires_checksum_asset_for_web_update(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

        marker = _runtime_marker()

        def opener(request, timeout):
            return FakeHTTPResponse(
                {
                    "tag_name": "v0.1.6",
                    "html_url": "https://github.com/owner/project/releases/tag/v0.1.6",
                    "assets": [
                        {
                            "name": f"genapi_0.1.6_{marker}.tar.gz",
                            "browser_download_url": f"https://github.com/owner/project/releases/download/v0.1.6/genapi_0.1.6_{marker}.tar.gz",
                            "size": 1024,
                        },
                    ],
                }
            )

        status = build_release_status(
            current_version="0.1.5",
            settings=UpdateSettings(repo="owner/project", deployment_mode="systemd-binary", build_type="release"),
            opener=opener,
            force=True,
        )

        self.assertTrue(status["update_available"])
        self.assertFalse(status["can_update"])
        self.assertIn("checksums.txt", status["warning"])

    def test_release_status_checks_github_in_source_mode_without_enable_gate(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

        calls: list[str] = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeHTTPResponse(
                {
                    "tag_name": "v0.1.4",
                    "html_url": "https://github.com/owner/project/releases/tag/v0.1.4",
                    "assets": [],
                }
            )

        status = build_release_status(
            current_version="0.1.3",
            settings=UpdateSettings(repo="owner/project", deployment_mode="source", build_type="source"),
            opener=opener,
        )

        self.assertEqual(calls, ["https://api.github.com/repos/owner/project/releases/latest"])
        self.assertTrue(status["enabled"])
        self.assertTrue(status["update_available"])
        self.assertTrue(status["has_update"])
        self.assertFalse(status["can_update"])
        self.assertEqual(status["mode"], "source")
        self.assertEqual(status["deployment_mode"], "source")
        self.assertNotIn("disabled_reason", status)

    def test_release_status_allows_docker_update_without_binary_assets(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

        def opener(request, timeout):
            return FakeHTTPResponse(
                {
                    "tag_name": "v0.1.6",
                    "html_url": "https://github.com/owner/project/releases/tag/v0.1.6",
                    "assets": [],
                }
            )

        status = build_release_status(
            current_version="0.1.5",
            settings=UpdateSettings(
                repo="owner/project",
                deployment_mode="docker",
                build_type="docker",
                compose_dir="/deploy",
                compose_file="docker-compose.yml",
                service="app",
            ),
            opener=opener,
            force=True,
        )

        self.assertTrue(status["update_available"])
        self.assertTrue(status["has_update"])
        self.assertTrue(status["can_update"])
        self.assertEqual(status["deployment_mode"], "docker")
        self.assertEqual(status["build_type"], "docker")
        self.assertEqual(status["compose_dir"], "/deploy")
        self.assertEqual(status["compose_file"], "docker-compose.yml")
        self.assertEqual(status["service"], "app")

    def test_release_status_uses_cache_until_force_refresh(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

        calls: list[str] = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeHTTPResponse({"tag_name": f"v0.1.{3 + len(calls)}"})

        settings = UpdateSettings(repo="owner/cache-test", deployment_mode="systemd-binary", build_type="release")

        first = build_release_status(current_version="0.1.3", settings=settings, opener=opener)
        second = build_release_status(current_version="0.1.3", settings=settings, opener=opener)
        forced = build_release_status(current_version="0.1.3", settings=settings, opener=opener, force=True)

        self.assertEqual(first["latest_tag"], "v0.1.4")
        self.assertEqual(second["latest_tag"], "v0.1.4")
        self.assertEqual(forced["latest_tag"], "v0.1.5")
        self.assertEqual(len(calls), 2)

    def test_release_status_network_error_preserves_core_fields(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

        def opener(request, timeout):
            raise OSError("network down")

        status = build_release_status(
            current_version="0.1.3",
            settings=UpdateSettings(repo="owner/project", deployment_mode="systemd-binary", build_type="release"),
            opener=opener,
            force=True,
        )

        self.assertTrue(status["enabled"])
        self.assertFalse(status["can_update"])
        self.assertFalse(status["update_available"])
        self.assertFalse(status["has_update"])
        self.assertEqual(status["mode"], "systemd-binary")
        self.assertEqual(status["deployment_mode"], "systemd-binary")
        self.assertEqual(status["build_type"], "release")
        self.assertEqual(status["current_version"], "0.1.3")
        self.assertEqual(status["latest_version"], "")
        self.assertEqual(status["latest_tag"], "")
        self.assertEqual(status["release_url"], "")
        self.assertEqual(status["release_info"], {})
        self.assertEqual(status["repo"], "owner/project")
        self.assertIn("Failed to check GitHub Release", status["error"])

    def test_update_job_store_merges_helper_status_and_logs(self) -> None:
        from services.update_service import UpdateJobStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UpdateJobStore(Path(tmp_dir))
            job = store.create(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="admin-id",
            )
            job_dir = Path(tmp_dir) / "update-jobs" / job["id"]
            (job_dir / "status.json").write_text(
                json.dumps({"status": "succeeded", "finished_at": "2026-04-29T12:30:00Z"}),
                encoding="utf-8",
            )
            (job_dir / "log.txt").write_text("line one\nline two\n", encoding="utf-8")

            loaded = store.get(job["id"])

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["status"], "succeeded")
            self.assertEqual(loaded["finished_at"], "2026-04-29T12:30:00Z")
            self.assertEqual(loaded["logs"], ["line one", "line two"])

    def test_update_job_store_rejects_concurrent_active_job_creation(self) -> None:
        from services.update_service import UpdateJobStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UpdateJobStore(Path(tmp_dir))
            first = store.create_if_idle(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="admin-id",
            )
            second = store.create_if_idle(
                target_version="0.1.5",
                target_tag="v0.1.5",
                release_url="https://github.com/owner/project/releases/tag/v0.1.5",
                actor_id="admin-id-2",
            )

            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_update_job_store_keeps_existing_lock_without_active_job(self) -> None:
        from services.update_service import UpdateJobStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UpdateJobStore(Path(tmp_dir))
            store.jobs_dir.mkdir(parents=True, exist_ok=True)
            store.active_lock_path.write_text("claimed-by-other-request", encoding="utf-8")

            job = store.create_if_idle(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="admin-id",
            )

            self.assertIsNone(job)
            self.assertEqual(store.active_lock_path.read_text(encoding="utf-8"), "claimed-by-other-request")

    def test_update_job_store_recovers_terminal_helper_status_without_prior_poll(self) -> None:
        from services.update_service import UpdateJobStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UpdateJobStore(Path(tmp_dir))
            job = store.create_if_idle(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="admin-id",
            )
            self.assertIsNotNone(job)
            job_dir = Path(tmp_dir) / "update-jobs" / job["id"]
            (job_dir / "status.json").write_text(
                json.dumps({"status": "succeeded", "finished_at": "2026-04-29T12:30:00Z"}),
                encoding="utf-8",
            )

            next_job = store.create_if_idle(
                target_version="0.1.5",
                target_tag="v0.1.5",
                release_url="https://github.com/owner/project/releases/tag/v0.1.5",
                actor_id="admin-id-2",
            )

            self.assertIsNotNone(next_job)
            self.assertEqual(next_job["target_version"], "0.1.5")
            self.assertTrue(store.active_lock_path.exists())

    def test_update_job_store_releases_active_lock_when_helper_status_is_terminal(self) -> None:
        from services.update_service import UpdateJobStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UpdateJobStore(Path(tmp_dir))
            job = store.create_if_idle(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="admin-id",
            )
            self.assertIsNotNone(job)
            self.assertTrue(store.active_lock_path.exists())
            job_dir = Path(tmp_dir) / "update-jobs" / job["id"]
            (job_dir / "status.json").write_text(
                json.dumps({"status": "succeeded", "finished_at": "2026-04-29T12:30:00Z"}),
                encoding="utf-8",
            )

            loaded = store.get(job["id"])
            self.assertEqual(loaded["status"], "succeeded")
            self.assertFalse(store.active_lock_path.exists())

            next_job = store.create_if_idle(
                target_version="0.1.5",
                target_tag="v0.1.5",
                release_url="https://github.com/owner/project/releases/tag/v0.1.5",
                actor_id="admin-id-2",
            )

            self.assertIsNotNone(next_job)
            self.assertTrue(store.active_lock_path.exists())

    def test_update_job_store_keeps_active_lock_when_helper_status_is_active(self) -> None:
        from services.update_service import UpdateJobStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UpdateJobStore(Path(tmp_dir))
            job = store.create_if_idle(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="admin-id",
            )
            self.assertIsNotNone(job)
            job_dir = Path(tmp_dir) / "update-jobs" / job["id"]
            (job_dir / "status.json").write_text(
                json.dumps({"status": "running", "message": "helper still running"}),
                encoding="utf-8",
            )

            loaded = store.get(job["id"])
            next_job = store.create_if_idle(
                target_version="0.1.5",
                target_tag="v0.1.5",
                release_url="https://github.com/owner/project/releases/tag/v0.1.5",
                actor_id="admin-id-2",
            )

            self.assertEqual(loaded["status"], "running")
            self.assertTrue(store.active_lock_path.exists())
            self.assertIsNone(next_job)

    def test_update_job_store_recovers_stale_active_job(self) -> None:
        from services.update_service import UpdateJobStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UpdateJobStore(Path(tmp_dir), active_job_stale_seconds=60)
            job = store.create_if_idle(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="admin-id",
            )
            self.assertIsNotNone(job)
            job_dir = Path(tmp_dir) / "update-jobs" / job["id"]
            job_path = job_dir / "job.json"
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            payload["status"] = "running"
            payload["updated_at"] = "2026-04-29T12:00:00Z"
            job_path.write_text(json.dumps(payload), encoding="utf-8")

            with patch("services.update_service._utc_now", return_value="2026-04-29T12:02:01Z"):
                next_job = store.create_if_idle(
                    target_version="0.1.5",
                    target_tag="v0.1.5",
                    release_url="https://github.com/owner/project/releases/tag/v0.1.5",
                    actor_id="admin-id-2",
                )

            self.assertIsNotNone(next_job)
            stale_job = store.get(job["id"])
            self.assertEqual(stale_job["status"], "failed")
            self.assertIn("stale", stale_job["error"])

    def test_update_job_store_ignores_partial_helper_status_json(self) -> None:
        from services.update_service import UpdateJobStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UpdateJobStore(Path(tmp_dir))
            job = store.create(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="admin-id",
            )
            job_dir = Path(tmp_dir) / "update-jobs" / job["id"]
            (job_dir / "status.json").write_text('{"status": "running"', encoding="utf-8")

            loaded = store.get(job["id"])

            self.assertEqual(loaded["status"], "pending")
            self.assertIn("status_error", loaded)


def _runtime_marker() -> str:
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        return "linux_arm64"
    return "linux_amd64"


if __name__ == "__main__":
    unittest.main()
