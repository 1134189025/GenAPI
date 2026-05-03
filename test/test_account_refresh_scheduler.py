from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import support
from services.account_service import AccountService
from services.config import ConfigStore
from services.storage.json_storage import JSONStorageBackend


class AccountRefreshSchedulerTests(unittest.TestCase):
    def test_limited_account_watcher_reads_current_interval_each_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_store = ConfigStore(Path(tmp_dir) / "config.json")
            config_store.update({"refresh_account_interval_minute": 1})
            wait_seconds: list[int] = []

            class StopAfterTwoWaits:
                def is_set(self) -> bool:
                    return len(wait_seconds) >= 2

                def wait(self, seconds: float) -> bool:
                    wait_seconds.append(int(seconds))
                    if len(wait_seconds) == 1:
                        config_store.update({"refresh_account_interval_minute": 2})
                    return False

            with (
                patch.object(support, "config", config_store),
                patch.object(support.account_service, "list_limited_tokens", return_value=[]),
                patch.object(support.account_service, "list_problem_tokens", return_value=[], create=True),
            ):
                thread = support.start_limited_account_watcher(StopAfterTwoWaits())
                thread.join(timeout=2)

            self.assertFalse(thread.is_alive())
            self.assertEqual(wait_seconds, [60, 120])

    def test_refresh_interval_clamps_non_positive_saved_values_to_one_minute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_store = ConfigStore(Path(tmp_dir) / "config.json")

            for invalid_interval in (0, -3, "0", "-2"):
                saved = config_store.update({"refresh_account_interval_minute": invalid_interval})

                self.assertEqual(saved["refresh_account_interval_minute"], 1)
                self.assertEqual(config_store.refresh_account_interval_minute, 1)

    def test_list_problem_tokens_includes_limited_abnormal_and_zero_quota_dirty_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts(["limited", "abnormal", "dirty-zero", "disabled", "healthy", "unknown"])
            service.update_account("limited", {"status": "限流", "quota": 0})
            service.update_account("abnormal", {"status": "异常", "quota": 0})
            service.update_account("dirty-zero", {"status": "正常", "quota": 0, "image_quota_unknown": False})
            service.update_account("disabled", {"status": "禁用", "quota": 0})
            service.update_account("healthy", {"status": "正常", "quota": 3})
            service.update_account("unknown", {"status": "正常", "quota": 0, "image_quota_unknown": True})

            self.assertEqual(set(service.list_problem_tokens()), {"limited", "abnormal", "dirty-zero"})


if __name__ == "__main__":
    unittest.main()
