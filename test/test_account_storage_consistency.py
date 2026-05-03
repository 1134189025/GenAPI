from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.accounts import create_router
from services.account_service import AccountService
from services.storage.database_storage import AccountModel, DatabaseStorageBackend
from services.storage.json_storage import JSONStorageBackend


class AccountTokenPrivacyTests(unittest.TestCase):
    def test_list_accounts_hides_raw_token_but_export_accounts_returns_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            token = "raw-secret-access-token"
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts([token])

            listed = service.list_accounts()
            with patch.object(AccountService.export_accounts.__globals__["log_service"], "add") as log_mock:
                exported = service.export_accounts()

            self.assertEqual(len(listed), 1)
            self.assertIn("id", listed[0])
            self.assertNotEqual(listed[0].get("access_token"), token)
            self.assertNotIn(token, str(listed[0]))
            self.assertEqual(exported[0]["access_token"], token)
            log_mock.assert_called_once_with("account", "导出账号", {"count": 1, "token_refs": [listed[0]["token_ref"]]})

    def test_export_accounts_audit_log_includes_actor_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            token = "raw-secret-access-token"
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts([token])

            with patch.object(AccountService.export_accounts.__globals__["log_service"], "add") as log_mock:
                exported = service.export_accounts(
                    actor={
                        "id": "admin-id",
                        "email": "admin@example.com",
                        "name": "Admin User",
                        "role": "admin",
                    }
                )

            self.assertEqual(exported[0]["access_token"], token)
            log_mock.assert_called_once_with(
                "account",
                "导出账号",
                {
                    "count": 1,
                    "actor_id": "admin-id",
                        "actor_email": "admin@example.com",
                        "actor_name": "Admin User",
                        "actor_role": "admin",
                        "token_refs": [service.list_accounts()[0]["token_ref"]],
                    },
            )

    def test_token_id_can_update_refresh_and_delete_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            token = "raw-secret-access-token"
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts([token])
            token_id = service.list_accounts()[0]["id"]

            updated = service.update_account(token_id, {"quota": 7})
            with patch.object(service, "fetch_remote_info", return_value={"quota": 8, "status": "正常"}):
                refreshed = service.refresh_accounts([token_id])
            deleted = service.delete_accounts([token_id])

            self.assertIsNotNone(updated)
            self.assertEqual(updated["quota"], 7)
            self.assertEqual(refreshed["refreshed"], 1)
            self.assertEqual(deleted["removed"], 1)
            self.assertEqual(service.list_tokens(), [])

    def test_token_ref_can_update_refresh_and_delete_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            token = "raw-secret-access-token"
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts([token])
            token_ref = service.list_accounts()[0]["token_ref"]

            updated = service.update_account(token_ref, {"quota": 7})
            with patch.object(service, "fetch_remote_info", return_value={"quota": 8, "status": "正常"}):
                refreshed = service.refresh_accounts([token_ref])
            deleted = service.delete_accounts([token_ref])

            self.assertIsNotNone(updated)
            self.assertEqual(updated["quota"], 7)
            self.assertEqual(refreshed["refreshed"], 1)
            self.assertEqual(deleted["removed"], 1)
            self.assertEqual(service.list_tokens(), [])

    def test_accounts_export_route_is_explicit_raw_token_surface(self) -> None:
        app = FastAPI()
        app.include_router(create_router())
        token = "raw-secret-export-token"
        identity = {"id": "admin-id", "email": "admin@example.com", "name": "Admin User", "role": "admin"}
        account_service_obj = create_router.__globals__["account_service"]

        with (
            patch.dict(create_router.__globals__, {"require_admin": lambda authorization: identity}),
            patch.object(account_service_obj, "list_accounts", return_value=[{"id": "token-id", "access_token": "token:masked"}]),
            patch.object(account_service_obj, "export_accounts", return_value=[{"access_token": token}]) as export_mock,
        ):
            with TestClient(app) as client:
                list_response = client.get("/api/accounts", headers={"Authorization": "Bearer admin"})
                export_response = client.get(
                    "/api/accounts/export",
                    headers={"Authorization": "Bearer admin", "User-Agent": "unit-test-agent"},
                )

        self.assertEqual(list_response.status_code, 200, list_response.text)
        self.assertEqual(export_response.status_code, 200, export_response.text)
        self.assertNotIn(token, list_response.text)
        self.assertEqual(export_response.json()["items"][0]["access_token"], token)
        self.assertEqual(export_response.headers["cache-control"], "no-store")
        export_mock.assert_called_once_with(actor=identity, request_ip="testclient", user_agent="unit-test-agent")

    def test_account_update_route_does_not_return_raw_token_in_item(self) -> None:
        app = FastAPI()
        app.include_router(create_router())
        token = "raw-secret-update-token"
        public_item = {"id": "token-id", "access_token": "token:masked", "quota": 3}

        account_service_obj = create_router.__globals__["account_service"]
        with (
            patch.dict(create_router.__globals__, {"require_admin": lambda authorization: {"role": "admin"}}),
            patch.object(account_service_obj, "update_account", return_value={"access_token": token, "quota": 3}),
            patch.object(account_service_obj, "public_account", return_value=public_item),
            patch.object(account_service_obj, "list_accounts", return_value=[public_item]),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/accounts/update",
                    headers={"Authorization": "Bearer admin"},
                    json={"account_id": "token-id", "quota": 3},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(token, response.text)
        self.assertEqual(response.json()["item"], public_item)

    def test_account_operation_routes_accept_ids_and_token_refs(self) -> None:
        app = FastAPI()
        app.include_router(create_router())
        token_ref = "token:masked"
        public_item = {"id": "token-id", "token_ref": token_ref, "access_token": token_ref, "quota": 3}

        account_service_obj = create_router.__globals__["account_service"]
        with (
            patch.dict(create_router.__globals__, {"require_admin": lambda authorization: {"role": "admin"}}),
            patch.object(account_service_obj, "delete_accounts", return_value={"removed": 1, "items": []}) as delete_mock,
            patch.object(
                account_service_obj,
                "refresh_accounts",
                return_value={"refreshed": 1, "errors": [], "items": [public_item]},
            ) as refresh_mock,
            patch.object(account_service_obj, "update_account", return_value={"access_token": "raw-secret", "quota": 4}) as update_mock,
            patch.object(account_service_obj, "public_account", return_value={**public_item, "quota": 4}),
            patch.object(account_service_obj, "list_accounts", return_value=[{**public_item, "quota": 4}]),
        ):
            with TestClient(app) as client:
                delete_response = client.request(
                    "DELETE",
                    "/api/accounts",
                    headers={"Authorization": "Bearer admin"},
                    json={"account_ids": ["token-id"], "token_refs": [token_ref]},
                )
                refresh_response = client.post(
                    "/api/accounts/refresh",
                    headers={"Authorization": "Bearer admin"},
                    json={"account_ids": ["token-id"], "token_refs": [token_ref]},
                )
                update_response = client.post(
                    "/api/accounts/update",
                    headers={"Authorization": "Bearer admin"},
                    json={"account_id": "token-id", "token_ref": token_ref, "quota": 4},
                )

        self.assertEqual(delete_response.status_code, 200, delete_response.text)
        self.assertEqual(refresh_response.status_code, 200, refresh_response.text)
        self.assertEqual(update_response.status_code, 200, update_response.text)
        delete_mock.assert_called_once_with(["token-id", token_ref])
        refresh_mock.assert_called_once_with(["token-id", token_ref])
        update_mock.assert_called_once_with("token-id", {"quota": 4})


class AccountRefreshRedactionTests(unittest.TestCase):
    def test_refresh_errors_keep_access_token_key_without_leaking_raw_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            token = "raw-secret-access-token"
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts([token])

            with patch.object(service, "fetch_remote_info", side_effect=RuntimeError(f"boom {token}")):
                result = service.refresh_accounts([token])

            self.assertEqual(len(result["errors"]), 1)
            self.assertIn("access_token", result["errors"][0])
            self.assertNotEqual(result["errors"][0]["access_token"], token)
            self.assertNotIn(token, str(result["errors"][0]))

    def test_token_invalid_error_marks_account_abnormal_when_auto_remove_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            token = "raw-secret-invalid-token"
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts([token])
            service.update_account(token, {"status": "正常", "quota": 5})

            with (
                patch.dict("services.account_service.config.data", {"auto_remove_invalid_accounts": False}),
                patch.object(
                    service,
                    "fetch_remote_info",
                    side_effect=RuntimeError("authentication token has been invalidated"),
                ),
            ):
                result = service.refresh_accounts([token])

            account = service.get_account(token)
            self.assertEqual(result["refreshed"], 0)
            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(service.list_tokens(), [token])
            self.assertIsNotNone(account)
            self.assertEqual(account["status"], "异常")
            self.assertEqual(account["quota"], 0)
            self.assertFalse(AccountService._is_image_account_available(account))
            self.assertFalse(service.has_available_account())

    def test_rate_limit_error_marks_account_limited_during_batch_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            token = "raw-secret-limited-token"
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts([token])
            service.update_account(token, {"status": "正常", "quota": 5})

            with patch.object(service, "fetch_remote_info", side_effect=RuntimeError("status=429 usage limit reached")):
                result = service.refresh_accounts([token])

            account = service.get_account(token)
            self.assertEqual(result["refreshed"], 0)
            self.assertEqual(len(result["errors"]), 1)
            self.assertIsNotNone(account)
            self.assertEqual(account["status"], "限流")
            self.assertEqual(account["quota"], 0)
            self.assertFalse(AccountService._is_image_account_available(account))


class JSONAccountStorageTests(unittest.TestCase):
    def test_corrupt_existing_json_raises_and_is_not_overwritten_by_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "accounts.json"
            path.write_text("{not json", encoding="utf-8")
            backend = JSONStorageBackend(path)

            with self.assertRaises(ValueError):
                backend.load_accounts()

            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_saved_accounts_file_is_owner_read_write_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "accounts.json"
            backend = JSONStorageBackend(path)

            backend.save_accounts([{"access_token": "token-a"}])

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class DatabaseAccountStorageTests(unittest.TestCase):
    def test_save_accounts_upserts_without_deleting_concurrent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            backend = DatabaseStorageBackend(f"sqlite:///{Path(tmp_dir) / 'accounts.db'}")
            backend.save_accounts([{"access_token": "token-a", "quota": 1}])

            session = backend.Session()
            try:
                session.add(AccountModel(access_token="token-b", data='{"access_token":"token-b","quota":2}'))
                session.commit()
            finally:
                session.close()

            backend.save_accounts([{"access_token": "token-a", "quota": 3}])

            loaded = {item["access_token"]: item for item in backend.load_accounts()}
            self.assertEqual(loaded["token-a"]["quota"], 3)
            self.assertEqual(loaded["token-b"]["quota"], 2)

    def test_replace_accounts_explicitly_removes_rows_missing_from_replacement_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            backend = DatabaseStorageBackend(f"sqlite:///{Path(tmp_dir) / 'accounts.db'}")
            backend.save_accounts([
                {"access_token": "token-a", "quota": 1},
                {"access_token": "token-b", "quota": 2},
            ])

            backend.replace_accounts([{"access_token": "token-a", "quota": 3}])

            loaded = {item["access_token"]: item for item in backend.load_accounts()}
            self.assertEqual(set(loaded), {"token-a"})
            self.assertEqual(loaded["token-a"]["quota"], 3)

    def test_database_backed_account_service_delete_removes_requested_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(DatabaseStorageBackend(f"sqlite:///{Path(tmp_dir) / 'accounts.db'}"))
            service.add_accounts(["token-a", "token-b"])

            result = service.delete_accounts(["token-a"])

            self.assertEqual(result["removed"], 1)
            self.assertEqual(service.list_tokens(), ["token-b"])
            reloaded = AccountService(DatabaseStorageBackend(f"sqlite:///{Path(tmp_dir) / 'accounts.db'}"))
            self.assertEqual(reloaded.list_tokens(), ["token-b"])

    def test_auto_removed_rate_limited_database_account_does_not_reappear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            backend_url = f"sqlite:///{Path(tmp_dir) / 'accounts.db'}"
            service = AccountService(DatabaseStorageBackend(backend_url))
            service.add_accounts(["token-a"])

            account_config = AccountService.update_account.__globals__["config"]
            with patch.dict(account_config.data, {"auto_remove_rate_limited_accounts": True}):
                result = service.update_account("token-a", {"status": "限流", "quota": 0})

            self.assertIsNone(result)
            reloaded = AccountService(DatabaseStorageBackend(backend_url))
            self.assertEqual(reloaded.list_tokens(), [])

    def test_database_backed_stale_service_snapshot_does_not_restore_deleted_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            backend_url = f"sqlite:///{Path(tmp_dir) / 'accounts.db'}"
            first = AccountService(DatabaseStorageBackend(backend_url))
            first.add_accounts(["token-a", "token-b"])
            second = AccountService(DatabaseStorageBackend(backend_url))
            second.delete_accounts(["token-a"])

            first.add_accounts(["token-c"])

            reloaded = AccountService(DatabaseStorageBackend(backend_url))
            self.assertEqual(set(reloaded.list_tokens()), {"token-b", "token-c"})


class MigrationReplaceTests(unittest.TestCase):
    def test_import_replace_uses_explicit_replacement_api(self) -> None:
        from scripts import migrate_storage

        class FakeStorage:
            def __init__(self) -> None:
                self.saved: list | None = None
                self.replaced: list | None = None

            def save_accounts(self, accounts: list[dict]) -> None:
                self.saved = accounts

            def replace_accounts(self, accounts: list[dict]) -> None:
                self.replaced = accounts

            def load_accounts(self) -> list[dict]:
                return []

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "accounts.json"
            input_path.write_text('[{"access_token":"token-a"}]\n', encoding="utf-8")
            storage = FakeStorage()

            with patch.object(migrate_storage, "create_storage_backend", return_value=storage):
                migrate_storage.import_from_json(str(input_path), replace=True)

            self.assertIsNone(storage.saved)
            self.assertEqual(storage.replaced, [{"access_token": "token-a"}])

    def test_import_without_replace_merges_existing_accounts(self) -> None:
        from scripts import migrate_storage

        class FakeStorage:
            def __init__(self) -> None:
                self.saved: list | None = None
                self.replaced: list | None = None

            def load_accounts(self) -> list[dict]:
                return [
                    {"access_token": "token-a", "quota": 1},
                    {"access_token": "token-b", "quota": 2},
                ]

            def save_accounts(self, accounts: list[dict]) -> None:
                self.saved = accounts

            def replace_accounts(self, accounts: list[dict]) -> None:
                self.replaced = accounts

        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "accounts.json"
            input_path.write_text('[{"access_token":"token-a","quota":3}]\n', encoding="utf-8")
            storage = FakeStorage()

            with patch.object(migrate_storage, "create_storage_backend", return_value=storage):
                migrate_storage.import_from_json(str(input_path), replace=False)

            self.assertIsNone(storage.replaced)
            self.assertEqual(
                storage.saved,
                [
                    {"access_token": "token-a", "quota": 3},
                    {"access_token": "token-b", "quota": 2},
                ],
            )


if __name__ == "__main__":
    unittest.main()
