from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


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
    def test_release_status_detects_newer_semver_release(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

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
                }
            )

        status = build_release_status(
            current_version="0.1.3",
            settings=UpdateSettings(repo="owner/project", enabled=True),
            opener=opener,
        )

        self.assertTrue(status["update_available"])
        self.assertTrue(status["enabled"])
        self.assertEqual(status["current_version"], "0.1.3")
        self.assertEqual(status["latest_version"], "0.1.4")
        self.assertEqual(status["latest_tag"], "v0.1.4")
        self.assertEqual(status["release_url"], "https://github.com/owner/project/releases/tag/v0.1.4")

    def test_release_status_treats_disabled_updater_as_manual_only(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

        status = build_release_status(
            current_version="0.1.4",
            settings=UpdateSettings(repo="owner/project", enabled=False),
            opener=lambda request, timeout: FakeHTTPResponse({"tag_name": "v0.1.4"}),
        )

        self.assertFalse(status["enabled"])
        self.assertFalse(status["update_available"])
        self.assertEqual(status["mode"], "manual")
        self.assertIn("GENAPI_ENABLE_WEB_UPDATER", status["disabled_reason"])

    def test_release_status_uses_cache_until_force_refresh(self) -> None:
        from services.update_service import UpdateSettings, build_release_status

        calls: list[str] = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeHTTPResponse({"tag_name": f"v0.1.{3 + len(calls)}"})

        settings = UpdateSettings(repo="owner/cache-test", enabled=True)

        first = build_release_status(current_version="0.1.3", settings=settings, opener=opener)
        second = build_release_status(current_version="0.1.3", settings=settings, opener=opener)
        forced = build_release_status(current_version="0.1.3", settings=settings, opener=opener, force=True)

        self.assertEqual(first["latest_tag"], "v0.1.4")
        self.assertEqual(second["latest_tag"], "v0.1.4")
        self.assertEqual(forced["latest_tag"], "v0.1.5")
        self.assertEqual(len(calls), 2)

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


if __name__ == "__main__":
    unittest.main()
