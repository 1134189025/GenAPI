from __future__ import annotations

import hashlib
import io
import os
import platform
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeBytesResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self.payload)
        chunk = self.payload[:size]
        self.payload = self.payload[size:]
        return chunk


class UpdateExecutorTests(unittest.TestCase):
    def test_preflight_validates_binary_release_mode_executable_and_release_asset(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = _make_release_layout(Path(tmp_dir), "0.1.5", binary=b"old binary", web_files={"index.html": b"old"})
            release_info = {"assets": [_asset("genapi_v0.1.6", b"unused")]}

            ok = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(deployment_mode="systemd-binary", build_type="release"),
                executable_path=executable,
            ).preflight(release_info={**release_info, "assets": [*release_info["assets"], {"name": "checksums.txt"}]})

            bad_mode = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(deployment_mode="source", build_type="release"),
                executable_path=executable,
            ).preflight(release_info=release_info)

            bad_build = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(deployment_mode="systemd-binary", build_type="source"),
                executable_path=executable,
            ).preflight(release_info=release_info)

            missing_asset = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(deployment_mode="systemd-binary", build_type="release"),
                executable_path=executable,
            ).preflight(release_info={"assets": []})
            missing_checksum = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(deployment_mode="systemd-binary", build_type="release"),
                executable_path=executable,
            ).preflight(release_info=release_info)

        self.assertTrue(ok["ok"], ok)
        self.assertFalse(bad_mode["ok"])
        self.assertTrue(any("systemd-binary" in error for error in bad_mode["errors"]))
        self.assertFalse(bad_build["ok"])
        self.assertTrue(any("GENAPI_BUILD_TYPE" in error for error in bad_build["errors"]))
        self.assertFalse(missing_asset["ok"])
        self.assertTrue(any("compatible" in error for error in missing_asset["errors"]))
        self.assertFalse(missing_checksum["ok"])
        self.assertTrue(any("checksums.txt" in error for error in missing_checksum["errors"]))

    def test_perform_update_downloads_verifies_extracts_and_replaces_release_payload_atomically(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        archive = _release_archive(
            "package",
            binary=b"new binary",
            version="0.1.6",
            web_files={"index.html": b"<h1>new</h1>", "_next/static/app.js": b"console.log('new')"},
        )
        asset = _asset("genapi_v0.1.6", archive)
        checksum = hashlib.sha256(archive).hexdigest()
        checksums = f"{checksum}  {asset['name']}\n".encode("utf-8")
        release_info = {"assets": [asset, _checksum_asset(checksums)]}

        with tempfile.TemporaryDirectory() as tmp_dir:
            install_dir = Path(tmp_dir)
            executable = _make_release_layout(install_dir, "0.1.5", binary=b"old binary", web_files={"index.html": b"<h1>old</h1>"})
            old_release = executable.resolve().parent
            executor = BinaryUpdateExecutor(
                data_dir=install_dir / "data",
                settings=UpdateSettings(
                    deployment_mode="systemd-binary",
                    build_type="release",
                    max_download_bytes=1024 * 1024,
                ),
                executable_path=executable,
                opener=_opener(
                    {
                        str(asset["download_url"]): archive,
                        str(release_info["assets"][1]["download_url"]): checksums,
                    }
                ),
            )

            result = executor.perform_update("v0.1.6", "0.1.6", release_info)

            current = (install_dir / "current").resolve()
            previous = (install_dir / "previous").resolve()
            self.assertEqual((install_dir / "current" / "genapi").read_bytes(), b"new binary")
            self.assertEqual((install_dir / "current" / "VERSION").read_text(encoding="utf-8"), "0.1.6\n")
            self.assertEqual((install_dir / "current" / "web_dist" / "index.html").read_bytes(), b"<h1>new</h1>")
            self.assertEqual((install_dir / "current" / "web_dist" / "_next" / "static" / "app.js").read_bytes(), b"console.log('new')")
            self.assertEqual((install_dir / "previous" / "genapi").read_bytes(), b"old binary")
            self.assertEqual(previous, old_release)
            self.assertNotEqual(current, previous)
            self.assertTrue(os.access(install_dir / "current" / "genapi", os.X_OK))

        self.assertTrue(result["ok"])
        self.assertEqual(result["target_tag"], "v0.1.6")
        self.assertEqual(result["target_version"], "0.1.6")
        self.assertEqual(result["asset"]["name"], asset["name"])

    def test_perform_update_rejects_second_update_before_restart(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        archive = _release_archive("package", binary=b"new binary", version="0.1.6", web_files={"index.html": b"new"})
        asset = _asset("genapi_v0.1.6", archive)
        checksum = hashlib.sha256(archive).hexdigest()
        checksums = f"{checksum}  {asset['name']}\n".encode("utf-8")
        release_info = {"assets": [asset, _checksum_asset(checksums)]}

        with tempfile.TemporaryDirectory() as tmp_dir:
            install_dir = Path(tmp_dir)
            executable = _make_release_layout(install_dir, "0.1.5", binary=b"old binary", web_files={"index.html": b"old"})
            running_executable = executable.resolve()
            settings = UpdateSettings(deployment_mode="systemd-binary", build_type="release", max_download_bytes=1024 * 1024)
            opener = _opener({str(asset["download_url"]): archive, str(release_info["assets"][1]["download_url"]): checksums})
            BinaryUpdateExecutor(
                data_dir=install_dir / "data",
                settings=settings,
                executable_path=running_executable,
                opener=opener,
            ).perform_update("v0.1.6", "0.1.6", release_info)

            with self.assertRaisesRegex(RuntimeError, "restart is required"):
                BinaryUpdateExecutor(
                    data_dir=install_dir / "data",
                    settings=settings,
                    executable_path=running_executable,
                    opener=opener,
                ).perform_update("v0.1.6", "0.1.6", release_info)

    def test_perform_update_rejects_untrusted_urls_and_oversized_assets(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = _make_release_layout(Path(tmp_dir), "0.1.5", binary=b"old binary", web_files={"index.html": b"old"})
            executor = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(
                    deployment_mode="systemd-binary",
                    build_type="release",
                    max_download_bytes=8,
                ),
                executable_path=executable,
                opener=_opener({}),
            )

            with self.assertRaisesRegex(ValueError, "HTTPS"):
                executor.perform_update(
                    "v0.1.6",
                    "0.1.6",
                    {
                        "assets": [
                            _asset("genapi_v0.1.6", b"payload", url="http://github.com/owner/repo/releases/download/v0.1.6/genapi.tar.gz"),
                            _checksum_asset(b""),
                        ]
                    },
                )

            with self.assertRaisesRegex(ValueError, "not allowed"):
                executor.perform_update(
                    "v0.1.6",
                    "0.1.6",
                    {
                        "assets": [
                            _asset("genapi_v0.1.6", b"payload", url="https://example.com/genapi.tar.gz"),
                            _checksum_asset(b""),
                        ]
                    },
                )

            with self.assertRaisesRegex(ValueError, "exceeds"):
                executor.perform_update("v0.1.6", "0.1.6", {"assets": [_asset("genapi_v0.1.6", b"too large"), _checksum_asset(b"")]})

    def test_perform_update_requires_checksums_asset(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        archive = _release_archive("package", binary=b"new binary", version="0.1.6", web_files={"index.html": b"new"})
        asset = _asset("genapi_v0.1.6", archive)

        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = _make_release_layout(Path(tmp_dir), "0.1.5", binary=b"old binary", web_files={"index.html": b"old"})
            executor = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(
                    deployment_mode="systemd-binary",
                    build_type="release",
                    max_download_bytes=1024 * 1024,
                ),
                executable_path=executable,
                opener=_opener({str(asset["download_url"]): archive}),
            )

            with self.assertRaisesRegex(RuntimeError, "checksums.txt"):
                executor.perform_update("v0.1.6", "0.1.6", {"assets": [asset]})

    def test_perform_update_rejects_checksum_mismatch_and_unsafe_tar_paths(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        archive = _tar_with_file("../genapi", b"new binary")
        asset = _asset("genapi_v0.1.6", archive)
        bad_checksums = f"{'0' * 64}  {asset['name']}\n".encode("utf-8")
        release_info = {"assets": [asset, _checksum_asset(bad_checksums)]}

        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = _make_release_layout(Path(tmp_dir), "0.1.5", binary=b"old binary", web_files={"index.html": b"old"})
            executor = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(
                    deployment_mode="systemd-binary",
                    build_type="release",
                    max_download_bytes=1024 * 1024,
                ),
                executable_path=executable,
                opener=_opener(
                    {
                        str(asset["download_url"]): archive,
                        str(release_info["assets"][1]["download_url"]): bad_checksums,
                    }
                ),
            )

            with self.assertRaisesRegex(ValueError, "checksum"):
                executor.perform_update("v0.1.6", "0.1.6", release_info)

            safe_checksum = hashlib.sha256(archive).hexdigest()
            safe_release_info = {
                "assets": [
                    asset,
                    _checksum_asset(f"{safe_checksum}  {asset['name']}\n".encode("utf-8")),
                ]
            }
            executor = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(
                    deployment_mode="systemd-binary",
                    build_type="release",
                    max_download_bytes=1024 * 1024,
                ),
                executable_path=executable,
                opener=_opener(
                    {
                        str(asset["download_url"]): archive,
                        str(safe_release_info["assets"][1]["download_url"]): safe_release_info["assets"][1]["content"],
                    }
                ),
            )

            with self.assertRaisesRegex(ValueError, "unsafe"):
                executor.perform_update("v0.1.6", "0.1.6", safe_release_info)

            self.assertEqual(executable.read_bytes(), b"old binary")

    def test_perform_update_restores_backup_when_replacement_fails(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        archive = _release_archive("package", binary=b"new binary", version="0.1.6", web_files={"index.html": b"new"})
        asset = _asset("genapi_v0.1.6", archive)
        checksum = hashlib.sha256(archive).hexdigest()
        checksums = f"{checksum}  {asset['name']}\n".encode("utf-8")
        release_info = {"assets": [asset, _checksum_asset(checksums)]}

        with tempfile.TemporaryDirectory() as tmp_dir:
            install_dir = Path(tmp_dir)
            executable = _make_release_layout(install_dir, "0.1.5", binary=b"old binary", web_files={"index.html": b"old"})
            real_replace = os.replace
            calls: list[tuple[str, str]] = []

            def flaky_replace(src, dst):
                calls.append((str(src), str(dst)))
                if len(calls) == 2:
                    raise OSError("simulated replace failure")
                return real_replace(src, dst)

            executor = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(
                    deployment_mode="systemd-binary",
                    build_type="release",
                    max_download_bytes=1024 * 1024,
                ),
                executable_path=executable,
                opener=_opener(
                    {
                        str(asset["download_url"]): archive,
                        str(release_info["assets"][1]["download_url"]): checksums,
                    }
                ),
            )

            with patch("services.update_executor.os.replace", side_effect=flaky_replace):
                with self.assertRaisesRegex(OSError, "simulated replace failure"):
                    executor.perform_update("v0.1.6", "0.1.6", release_info)

            self.assertEqual((install_dir / "current").resolve(), executable.resolve().parent)
            self.assertEqual((install_dir / "current" / "genapi").read_bytes(), b"old binary")
            self.assertEqual((install_dir / "current" / "VERSION").read_text(encoding="utf-8"), "0.1.5\n")
            self.assertEqual((install_dir / "current" / "web_dist" / "index.html").read_bytes(), b"old")

    def test_preflight_requires_release_symlink_layout_for_web_updates(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = Path(tmp_dir) / "genapi"
            executable.write_bytes(b"old binary")
            executable.chmod(0o755)

            result = BinaryUpdateExecutor(
                data_dir=Path(tmp_dir) / "data",
                settings=UpdateSettings(deployment_mode="systemd-binary", build_type="release"),
                executable_path=executable,
            ).preflight(release_info={"assets": [_asset("genapi_v0.1.6", b"unused"), {"name": "checksums.txt"}]})

        self.assertFalse(result["ok"])
        self.assertTrue(any("release layout" in error for error in result["errors"]))

    def test_rollback_restores_backup_binary(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        with tempfile.TemporaryDirectory() as tmp_dir:
            install_dir = Path(tmp_dir)
            executable = _make_release_layout(install_dir, "0.1.6", binary=b"new binary", web_files={"index.html": b"new"})
            previous = _create_release(install_dir / "releases", "0.1.5", binary=b"old binary", web_files={"index.html": b"old"})
            (install_dir / "previous").symlink_to(previous)
            executor = BinaryUpdateExecutor(
                data_dir=install_dir / "data",
                settings=UpdateSettings(deployment_mode="systemd-binary", build_type="release"),
                executable_path=executable,
            )

            result = executor.rollback()

            self.assertTrue(result["ok"])
            self.assertEqual((install_dir / "current").resolve(), previous)
            self.assertEqual((install_dir / "current" / "genapi").read_bytes(), b"old binary")
            self.assertEqual((install_dir / "current" / "VERSION").read_text(encoding="utf-8"), "0.1.5\n")
            self.assertEqual((install_dir / "current" / "web_dist" / "index.html").read_bytes(), b"old")

    def test_rollback_does_not_chmod_restored_root_owned_backup(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        with tempfile.TemporaryDirectory() as tmp_dir:
            install_dir = Path(tmp_dir)
            executable = _make_release_layout(install_dir, "0.1.6", binary=b"new binary", web_files={"index.html": b"new"})
            previous = _create_release(install_dir / "releases", "0.1.5", binary=b"old binary", web_files={"index.html": b"old"})
            (install_dir / "previous").symlink_to(previous)
            executor = BinaryUpdateExecutor(
                data_dir=install_dir / "data",
                settings=UpdateSettings(deployment_mode="systemd-binary", build_type="release"),
                executable_path=executable,
            )

            with patch.object(Path, "chmod", side_effect=PermissionError("not owner")):
                result = executor.rollback()

            self.assertTrue(result["ok"])
            self.assertEqual((install_dir / "current" / "genapi").read_bytes(), b"old binary")

    def test_restart_exits_process_for_systemd_restart_always(self) -> None:
        from services.update_executor import BinaryUpdateExecutor
        from services.update_service import UpdateSettings

        exit_codes: list[int] = []
        executor = BinaryUpdateExecutor(
            data_dir=Path("/tmp"),
            settings=UpdateSettings(deployment_mode="systemd-binary", build_type="release"),
            exit_func=lambda code: exit_codes.append(code),
            restart_delay_seconds=0,
        )

        result = executor.restart()

        self.assertTrue(result["ok"])
        self.assertEqual(exit_codes, [0])

    def test_docker_update_executor_name_remains_as_compatibility_alias(self) -> None:
        from services.update_executor import BinaryUpdateExecutor, DockerUpdateExecutor

        self.assertIs(DockerUpdateExecutor, BinaryUpdateExecutor)


def _runtime_marker() -> str:
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        return "linux_arm64"
    return "linux_amd64"


def _asset(prefix: str, content: bytes, url: str | None = None) -> dict[str, object]:
    name = f"{prefix}_{_runtime_marker()}.tar.gz"
    return {
        "name": name,
        "download_url": url or f"https://github.com/owner/repo/releases/download/v0.1.6/{name}",
        "size": len(content),
        "content": content,
    }


def _checksum_asset(content: bytes) -> dict[str, object]:
    return {
        "name": "checksums.txt",
        "download_url": "https://github.com/owner/repo/releases/download/v0.1.6/checksums.txt",
        "size": len(content),
        "content": content,
    }


def _tar_with_file(name: str, content: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def _release_archive(root: str, *, binary: bytes, version: str, web_files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        root_info = tarfile.TarInfo(name=root)
        root_info.type = tarfile.DIRTYPE
        root_info.mode = 0o755
        archive.addfile(root_info)

        for relative_name, content, mode in [
            ("genapi", binary, 0o755),
            ("VERSION", f"{version}\n".encode("utf-8"), 0o644),
        ]:
            info = tarfile.TarInfo(name=f"{root}/{relative_name}")
            info.size = len(content)
            info.mode = mode
            archive.addfile(info, io.BytesIO(content))

        web_root = tarfile.TarInfo(name=f"{root}/web_dist")
        web_root.type = tarfile.DIRTYPE
        web_root.mode = 0o755
        archive.addfile(web_root)
        created_dirs = {"web_dist"}
        for relative_name, content in web_files.items():
            parts = relative_name.split("/")[:-1]
            current = "web_dist"
            for part in parts:
                current = f"{current}/{part}"
                if current in created_dirs:
                    continue
                directory = tarfile.TarInfo(name=f"{root}/{current}")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                archive.addfile(directory)
                created_dirs.add(current)
            info = tarfile.TarInfo(name=f"{root}/web_dist/{relative_name}")
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def _make_release_layout(install_dir: Path, version: str, *, binary: bytes, web_files: dict[str, bytes]) -> Path:
    releases_dir = install_dir / "releases"
    release_dir = _create_release(releases_dir, version, binary=binary, web_files=web_files)
    current_link = install_dir / "current"
    current_link.symlink_to(release_dir)
    return current_link / "genapi"


def _create_release(releases_dir: Path, version: str, *, binary: bytes, web_files: dict[str, bytes]) -> Path:
    release_dir = releases_dir / f"release-{version}"
    web_dist = release_dir / "web_dist"
    web_dist.mkdir(parents=True, exist_ok=True)
    (release_dir / "genapi").write_bytes(binary)
    (release_dir / "genapi").chmod(0o755)
    (release_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    for relative_name, content in web_files.items():
        target = web_dist / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return release_dir


def _opener(payloads: dict[str, bytes]):
    def opener(request, timeout):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        return FakeBytesResponse(payloads[url])

    return opener


if __name__ == "__main__":
    unittest.main()
