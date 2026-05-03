from __future__ import annotations

import contextlib
import io
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from utils.helper import anonymize_token


class ImageStreamLoggingTests(unittest.TestCase):
    def test_image_stream_failure_logs_anonymized_token_ref_only(self) -> None:
        raw_token = "sk-raw-image-token-secret"
        logged: list[dict] = []

        def fail_stream(*_args, **_kwargs):
            raise RuntimeError(f"upstream failed for {raw_token}")
            yield

        from services.protocol import conversation

        with (
            patch.object(conversation.account_service, "get_available_access_token", return_value=raw_token),
            patch.object(conversation.account_service, "mark_image_result"),
            patch.object(conversation, "OpenAIBackendAPI"),
            patch.object(conversation, "stream_image_outputs", side_effect=fail_stream),
            patch.object(conversation.logger, "warning", side_effect=logged.append),
        ):
            with self.assertRaises(Exception):
                list(conversation.stream_image_outputs_with_pool(conversation.ConversationRequest(model="gpt-image-2", prompt="x")))

        self.assertEqual(logged[0]["event"], "image_stream_fail")
        self.assertEqual(logged[0]["token_ref"], anonymize_token(raw_token))
        self.assertNotIn("request_token", logged[0])
        self.assertNotIn(raw_token, str(logged[0]))

    def test_image_stream_failure_response_is_redacted(self) -> None:
        raw_token = "sk-raw-image-token-secret"

        def fail_stream(*_args, **_kwargs):
            raise RuntimeError(f"upstream failed for {raw_token}")
            yield

        from services.protocol import conversation

        with (
            patch.object(conversation.account_service, "get_available_access_token", return_value=raw_token),
            patch.object(conversation.account_service, "mark_image_result"),
            patch.object(conversation, "OpenAIBackendAPI"),
            patch.object(conversation, "stream_image_outputs", side_effect=fail_stream),
            patch.object(conversation.logger, "warning"),
        ):
            with self.assertRaises(Exception) as raised:
                list(conversation.stream_image_outputs_with_pool(conversation.ConversationRequest(model="gpt-image-2", prompt="x")))

        self.assertNotIn(raw_token, str(raised.exception))


class ImageStreamTokenRetryTests(unittest.TestCase):
    def test_invalid_or_rate_limited_image_token_is_marked_and_next_token_is_tried(self) -> None:
        from services.protocol import conversation

        cases = [
            ("status=429 usage limit reached", "mark_rate_limited_token"),
            ("HTTP 401 authentication token has been invalidated", "mark_invalid_token"),
        ]
        for error_message, marker_name in cases:
            with self.subTest(error_message=error_message):
                bad_token = f"bad-token-{marker_name}"
                good_token = f"good-token-{marker_name}"
                excluded_snapshots: list[set[str]] = []

                class FakeBackend:
                    def __init__(self, access_token: str = "") -> None:
                        self.access_token = access_token

                def get_token(*, excluded_tokens=None):
                    excluded = set(excluded_tokens or set())
                    excluded_snapshots.append(excluded)
                    return good_token if bad_token in excluded else bad_token

                def fake_stream(backend, request, index, total):
                    if backend.access_token == bad_token:
                        raise RuntimeError(error_message)
                    yield conversation.ImageOutput(
                        kind="result",
                        model=request.model,
                        index=index,
                        total=total,
                        data=[{"b64_json": "generated-image"}],
                    )

                with (
                    patch.object(conversation.account_service, "get_available_access_token", side_effect=get_token),
                    patch.object(conversation.account_service, "mark_image_result") as mark_result,
                    patch.object(conversation.account_service, "mark_invalid_token") as mark_invalid,
                    patch.object(conversation.account_service, "mark_rate_limited_token") as mark_limited,
                    patch.object(conversation, "OpenAIBackendAPI", FakeBackend),
                    patch.object(conversation, "stream_image_outputs", side_effect=fake_stream),
                    patch.object(conversation.logger, "warning"),
                ):
                    outputs = list(
                        conversation.stream_image_outputs_with_pool(
                            conversation.ConversationRequest(model="gpt-image-2", prompt="x")
                        )
                    )

                self.assertEqual([output.data for output in outputs], [[{"b64_json": "generated-image"}]])
                self.assertEqual(excluded_snapshots, [set(), {bad_token}])
                mark_result.assert_any_call(bad_token, False)
                mark_result.assert_any_call(good_token, True)
                if marker_name == "mark_rate_limited_token":
                    mark_limited.assert_called_once_with(bad_token, "image_stream")
                    mark_invalid.assert_not_called()
                else:
                    mark_invalid.assert_called_once_with(bad_token, "image_stream")
                    mark_limited.assert_not_called()


class ProxyErrorRedactionTests(unittest.TestCase):
    def test_proxy_test_error_does_not_return_embedded_credentials(self) -> None:
        from services import proxy_service

        proxy_url = "http://user:pass-secret@example.test:8080"

        class FakeSession:
            def __init__(self, **_kwargs):
                pass

            def get(self, *_args, **_kwargs):
                raise RuntimeError(f"failed via {proxy_url}")

            def close(self):
                pass

        with patch("services.proxy_service.Session", FakeSession):
            result = proxy_service.test_proxy(proxy_url, timeout=0.1)

        self.assertFalse(result["ok"])
        self.assertNotIn("pass-secret", result["error"])
        self.assertNotIn("user:pass-secret@", result["error"])


class ImportJobRedactionTests(unittest.TestCase):
    def test_sub2api_login_error_redacts_response_body_secrets(self) -> None:
        from services import sub2api_service

        class FakeResponse:
            ok = False
            status_code = 500
            text = '{"access_token":"raw-sub2api-token","password":"raw-sub2api-password"}'

        class FakeSession:
            def post(self, *_args, **_kwargs):
                return FakeResponse()

            def close(self):
                pass

        with patch.object(sub2api_service, "Session", return_value=FakeSession()):
            with self.assertRaises(RuntimeError) as raised:
                sub2api_service._login("http://sub2api.example.test", "admin@example.test", "secret")

        self.assertNotIn("raw-sub2api-token", str(raised.exception))
        self.assertNotIn("raw-sub2api-password", str(raised.exception))

    def test_cpa_fetch_error_redacts_secret_values(self) -> None:
        from services import cpa_service

        class FakeSession:
            def get(self, *_args, **_kwargs):
                raise RuntimeError('{"access_token":"raw-cpa-token","secret_key":"raw-cpa-key"}')

            def close(self):
                pass

        with patch.object(cpa_service, "Session", return_value=FakeSession()):
            token, error = cpa_service.fetch_remote_access_token(
                {"base_url": "http://cpa.example.test", "secret_key": "raw-cpa-key"},
                "account.json",
            )

        self.assertIsNone(token)
        self.assertNotIn("raw-cpa-token", error)
        self.assertNotIn("raw-cpa-key", error)


class CPARouteErrorHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.old_config_file = os.environ.get("GENAPI_CONFIG_FILE")
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        os.environ["GENAPI_CONFIG_FILE"] = str(base / "config.json")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{base / 'users.db'}"
        os.environ["JWT_SECRET"] = "unit-test-secret-with-at-least-32-bytes"
        self._clear_modules()
        app_module = importlib.import_module("api.app")
        self.accounts_module = importlib.import_module("api.accounts")
        self.client = TestClient(app_module.create_app())
        response = self.client.post(
            "/api/setup/admin",
            json={"email": "admin@example.com", "password": "AdminPass123!"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.headers = {"Authorization": f"Bearer {response.json()['token']}"}

    def tearDown(self) -> None:
        self.client.close()
        service_module = sys.modules.get("services.user_service")
        service = getattr(service_module, "user_service", None)
        engine = getattr(service, "engine", None)
        if engine is not None:
            engine.dispose()
        self._restore_env("GENAPI_CONFIG_FILE", self.old_config_file)
        self._restore_env("GENAPI_USER_DATABASE_URL", self.old_user_db)
        self._restore_env("JWT_SECRET", self.old_jwt_secret)
        self._clear_modules()
        self.tmp.cleanup()

    @staticmethod
    def _restore_env(key: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    @staticmethod
    def _clear_modules() -> None:
        for module_name in list(sys.modules):
            if module_name == "api" or module_name.startswith("api.") or module_name.startswith("services."):
                sys.modules.pop(module_name, None)

    def test_cpa_files_remote_errors_return_redacted_502(self) -> None:
        secret_key = "raw-cpa-management-secret"
        create_response = self.client.post(
            "/api/cpa/pools",
            headers=self.headers,
            json={
                "name": "CPA",
                "base_url": "http://cpa.example.test",
                "secret_key": secret_key,
            },
        )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        pool_id = create_response.json()["pool"]["id"]

        with patch.object(
            self.accounts_module,
            "list_remote_files",
            side_effect=RuntimeError(f"remote failed with {secret_key}"),
        ):
            response = self.client.get(f"/api/cpa/pools/{pool_id}/files", headers=self.headers)

        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn(secret_key, response.text)
        self.assertIn("error", response.json()["detail"])


class ImageErrorResponseRedactionTests(unittest.TestCase):
    def test_image_error_response_redacts_embedded_credentials(self) -> None:
        from services.log_service import _image_error_response

        response = _image_error_response(RuntimeError("upstream failed via http://user:pass-secret@example.test"))
        body = response.body.decode("utf-8")

        self.assertNotIn("pass-secret", body)
        self.assertNotIn("user:pass-secret@", body)

    def test_sse_stream_errors_are_redacted(self) -> None:
        from utils.helper import sse_json_stream

        secret = "http://user:pass-secret@example.test"

        def failing_items():
            raise RuntimeError(f"failed via {secret}")
            yield {}

        payload = "".join(sse_json_stream(failing_items()))

        self.assertNotIn("pass-secret", payload)
        self.assertNotIn("user:pass-secret@", payload)


class GitStorageSecurityTests(unittest.TestCase):
    def test_rejects_absolute_and_parent_traversal_file_paths(self) -> None:
        from services.storage.git_storage import GitStorageBackend

        with self.assertRaises(ValueError):
            GitStorageBackend("https://example.test/repo.git", "token", file_path="/tmp/accounts.json")
        with self.assertRaises(ValueError):
            GitStorageBackend("https://example.test/repo.git", "token", file_path="../accounts.json")

    def test_load_accounts_does_not_print_exception_with_auth_url(self) -> None:
        from services.storage.git_storage import GitStorageBackend

        backend = GitStorageBackend("https://example.test/repo.git", "secret-token", file_path="accounts.json")

        with patch.object(backend, "_clone_or_pull", side_effect=RuntimeError("https://secret-token@example.test/repo.git failed")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(RuntimeError):
                backend.load_accounts()

        self.assertEqual(output.getvalue(), "")


class TransportTlsTests(unittest.TestCase):
    def test_registration_transports_do_not_disable_tls_verification(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative_path in ("services/register/openai_register.py", "services/register/mail_provider.py"):
            source = (root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("verify=False", source, relative_path)


class SensitiveTextRedactionTests(unittest.TestCase):
    def test_redacts_bearer_and_api_key_material(self) -> None:
        from utils.helper import redact_sensitive_text

        text = "Authorization: Bearer raw-secret-token, x-api-key=raw-key-token, proxy=http://user:pass@example.test"
        redacted = redact_sensitive_text(text, ["raw-secret-token", "raw-key-token"])

        self.assertNotIn("raw-secret-token", redacted)
        self.assertNotIn("raw-key-token", redacted)
        self.assertNotIn("user:pass@", redacted)
        self.assertIn("Bearer ****", redacted)
        self.assertIn("x-api-key=****", redacted)

    def test_redacts_json_style_secret_fields(self) -> None:
        from utils.helper import redact_sensitive_text

        text = '{"access_token": "raw-json-token", "password": "raw-json-password"}'
        redacted = redact_sensitive_text(text)

        self.assertNotIn("raw-json-token", redacted)
        self.assertNotIn("raw-json-password", redacted)
        self.assertIn('"access_token": "****"', redacted)


if __name__ == "__main__":
    unittest.main()
