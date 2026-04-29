from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(text: str, expected: str, path: str) -> None:
    if expected not in text:
        raise AssertionError(f"{path} is missing expected text: {expected!r}")


def assert_not_contains(text: str, unexpected: str, path: str) -> None:
    if unexpected in text:
        raise AssertionError(f"{path} contains unsafe text: {unexpected!r}")


def assert_file(path: str) -> str:
    target = ROOT / path
    if not target.is_file():
        raise AssertionError(f"{path} does not exist")
    return target.read_text(encoding="utf-8")


def test_release_workflow() -> None:
    path = ".github/workflows/release.yml"
    text = assert_file(path)
    for expected in [
        "tags:",
        "- \"v*\"",
        "bun install --frozen-lockfile",
        "bun run build",
        "uv sync --frozen --no-dev --no-install-project",
        "uv run pyinstaller",
        "pyinstaller",
        "--onefile",
        "--paths \".\"",
        "--add-data \"VERSION:.\"",
        "--add-data \"web_dist:web_dist\"",
        "genapi_${VERSION}_linux_${ARCH}.tar.gz",
        "checksums.txt",
        "REPO: ${{ github.repository }}",
        "gh release view \"${TAG}\" --repo \"${REPO}\"",
        "gh release create \"${TAG}\" --repo \"${REPO}\"",
        "gh release upload",
        "gh release upload \"${TAG}\" --repo \"${REPO}\"",
        "ubuntu-24.04-arm",
    ]:
        assert_contains(text, expected, path)


def test_pyinstaller_entrypoint() -> None:
    path = "scripts/pyinstaller_entrypoint.py"
    text = assert_file(path)
    for expected in [
        "from main import main",
        "raise SystemExit(main())",
    ]:
        assert_contains(text, expected, path)


def test_systemd_unit() -> None:
    path = "deploy/genapi.service"
    text = assert_file(path)
    for expected in [
        "User=genapi",
        "Group=genapi",
        "WorkingDirectory=/opt/genapi/app/current",
        "ExecStart=/opt/genapi/app/current/genapi",
        "Restart=always",
        "ReadWritePaths=/opt/genapi/app /opt/genapi/data",
        "Environment=GENAPI_DEPLOYMENT_MODE=systemd-binary",
        "Environment=GENAPI_BUILD_TYPE=release",
        "Environment=GENAPI_DATA_DIR=/opt/genapi/data",
        "Environment=GENAPI_HOST=0.0.0.0",
        "Environment=GENAPI_PORT=3000",
        "Environment=GENAPI_UPDATE_REPO=1134189025/GenAPI",
    ]:
        assert_contains(text, expected, path)


def test_installer_contract() -> None:
    path = "deploy/install.sh"
    text = assert_file(path)
    for expected in [
        "install)",
        "upgrade)",
        "rollback)",
        "useradd",
        "/opt/genapi",
        "/opt/genapi/app",
        "/opt/genapi/app/releases",
        "/opt/genapi/data",
        "/etc/systemd/system/genapi.service",
        "genapi_${version}_linux_${arch}.tar.gz",
        "checksums.txt",
        "sha256sum -c",
        "GENAPI_INSTALL_SH_LIBRARY",
        "validate_archive \"${archive}\"",
        "safe_extract_archive \"${archive}\"",
        "BACKUP_DIR=\"/var/backups/genapi\"",
        "APP_DIR=\"/opt/genapi/app\"",
        "RELEASES_DIR=\"/opt/genapi/app/releases\"",
        "validate_payload_tree \"${payload_dir}\"",
        "tar -tzf",
        "tar -tzvf",
        "find -P \"${payload_dir}\"",
        "create_release_from_payload \"${payload_dir}\"",
        "activate_release \"${release_dir}\"",
        "WorkingDirectory=/opt/genapi/app/current",
        "ExecStart=/opt/genapi/app/current/genapi",
        "cp -R \"${payload_dir}/web_dist\"",
        "chmod u-s,g-s \"${release_dir}/genapi\"",
        "chmod 0755 \"${INSTALL_DIR}\"",
        "chmod 0775 \"${APP_DIR}\" \"${RELEASES_DIR}\"",
        "chmod 0700 \"${BACKUP_DIR}\"",
        "safe_extract_archive \"${backup}\"",
        "Environment=GENAPI_UPDATE_REPO=${REPO}",
        "systemctl enable genapi",
        "systemctl restart genapi",
    ]:
        assert_contains(text, expected, path)
    for unexpected in [
        'BACKUP_DIR="/opt/genapi/backups"',
        'tar -xzf "${archive}" -C "${TMP_DIR}"',
        'tar -xzf "${backup}" -C "${INSTALL_DIR}"',
        'chmod 0775 "${INSTALL_DIR}"',
        'chown -R genapi:genapi "${INSTALL_DIR}/web_dist"',
        'chmod -R u=rwX,g=rX,o= "${INSTALL_DIR}/web_dist"',
        'stop_service_if_present\n  prepare_dirs',
    ]:
        assert_not_contains(text, unexpected, path)

    validation_index = text.index('safe_extract_archive "${archive}" "${TMP_DIR}"')
    stop_index = text.index("  stop_service_if_present", validation_index)
    if validation_index > stop_index:
        raise AssertionError("installer stops the service before validating the downloaded archive")


def _write_symlink_escape_archive(path: Path) -> None:
    payload = b"outside"
    with tarfile.open(path, "w:gz") as archive:
        root = tarfile.TarInfo("genapi_0.1.6_linux_amd64")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        archive.addfile(root)

        link = tarfile.TarInfo("genapi_0.1.6_linux_amd64/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp"
        link.mode = 0o777
        archive.addfile(link)

        nested = tarfile.TarInfo("genapi_0.1.6_linux_amd64/link/escape")
        nested.type = tarfile.REGTYPE
        nested.mode = 0o644
        nested.size = len(payload)
        archive.addfile(nested, io.BytesIO(payload))


def test_installer_rejects_symlink_archives_before_extraction() -> None:
    path = "deploy/install.sh"
    assert_file(path)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "malicious.tar.gz"
        _write_symlink_escape_archive(archive)
        env = dict(os.environ)
        env["GENAPI_INSTALL_SH"] = str(ROOT / path)
        env["GENAPI_TEST_ARCHIVE"] = str(archive)
        result = subprocess.run(
            [
                "bash",
                "-c",
                'GENAPI_INSTALL_SH_LIBRARY=1 source "$GENAPI_INSTALL_SH"; validate_archive "$GENAPI_TEST_ARCHIVE"',
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            raise AssertionError("validate_archive accepted a symlink-containing archive")
        if "symlink" not in result.stderr.lower() and "special" not in result.stderr.lower():
            raise AssertionError(f"validate_archive failed for the wrong reason: {result.stderr!r}")


def main() -> None:
    tests = [
        test_release_workflow,
        test_pyinstaller_entrypoint,
        test_systemd_unit,
        test_installer_contract,
        test_installer_rejects_symlink_archives_before_extraction,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} packaging smoke tests passed")


if __name__ == "__main__":
    main()
