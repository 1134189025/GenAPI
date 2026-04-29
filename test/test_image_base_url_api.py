import unittest
from types import SimpleNamespace
from unittest import mock

import api.support as api_support


class ImageBaseUrlApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_config = SimpleNamespace(base_url="https://public.example.com")
        patcher = mock.patch.object(api_support, "config", self.fake_config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prefers_configured_base_url(self) -> None:
        request = SimpleNamespace(
            url=SimpleNamespace(scheme="http", netloc="127.0.0.1:8000"),
            headers={"host": "127.0.0.1:8000"},
        )

        self.assertEqual(api_support.resolve_image_base_url(request), "https://public.example.com")

    def test_falls_back_to_local_request_host(self) -> None:
        self.fake_config.base_url = ""
        request = SimpleNamespace(
            url=SimpleNamespace(scheme="http", netloc="127.0.0.1:8000"),
            headers={"host": "localhost:9000"},
        )

        self.assertEqual(api_support.resolve_image_base_url(request), "http://localhost:9000")

    def test_falls_back_to_local_request_netloc_when_host_missing(self) -> None:
        self.fake_config.base_url = ""
        request = SimpleNamespace(
            url=SimpleNamespace(scheme="https", netloc="127.0.0.1:8000"),
            headers={},
        )

        self.assertEqual(api_support.resolve_image_base_url(request), "https://127.0.0.1:8000")

    def test_does_not_trust_public_host_when_base_url_is_missing(self) -> None:
        self.fake_config.base_url = ""
        request = SimpleNamespace(
            url=SimpleNamespace(scheme="https", netloc="evil.example.com"),
            headers={"host": "evil.example.com"},
        )

        self.assertEqual(api_support.resolve_image_base_url(request), "")

    def test_rejects_configured_base_url_with_control_characters(self) -> None:
        self.fake_config.base_url = "https://public.example.com\r\nX-Injected: yes"
        request = SimpleNamespace(
            url=SimpleNamespace(scheme="https", netloc="safe.example.com"),
            headers={"host": "safe.example.com"},
        )

        self.assertEqual(api_support.resolve_image_base_url(request), "")

    def test_malformed_configured_base_url_falls_back_to_request_host(self) -> None:
        self.fake_config.base_url = "http://[::1"
        request = SimpleNamespace(
            url=SimpleNamespace(scheme="https", netloc="safe.example.com"),
            headers={"host": "safe.example.com"},
        )

        self.assertEqual(api_support.resolve_image_base_url(request), "")

    def test_ignores_host_header_with_url_injection(self) -> None:
        self.fake_config.base_url = ""
        request = SimpleNamespace(
            url=SimpleNamespace(scheme="https", netloc="localhost:8000"),
            headers={"host": "evil.example.com/path?x=1"},
        )

        self.assertEqual(api_support.resolve_image_base_url(request), "https://localhost:8000")


if __name__ == "__main__":
    unittest.main()
