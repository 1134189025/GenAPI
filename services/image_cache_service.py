from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Iterable


@dataclass(frozen=True)
class ImageCacheLimits:
    retention_days: int = 30
    max_size_mb: float = 10240
    auto_delete_enabled: bool = True

    @property
    def max_size_bytes(self) -> int:
        return max(1, int(float(self.max_size_mb or 0) * 1024 * 1024))

    @property
    def retention_seconds(self) -> int:
        return max(1, int(self.retention_days or 1)) * 86400


@dataclass(frozen=True)
class ImageCacheFile:
    path: Path
    size: int
    modified_at: float


def _iter_image_cache_files(root: Path) -> list[ImageCacheFile]:
    if not root.exists():
        return []
    files: list[ImageCacheFile] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append(ImageCacheFile(path=path, size=int(stat.st_size), modified_at=float(stat.st_mtime)))
    return files


def _remove_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def _protected_set(paths: Iterable[Path] | None) -> set[Path]:
    protected: set[Path] = set()
    for path in paths or set():
        try:
            protected.add(path.resolve())
        except OSError:
            protected.add(path)
    return protected


def get_image_cache_status(root: Path, limits: ImageCacheLimits) -> dict[str, Any]:
    files = _iter_image_cache_files(root)
    return {
        "total_size_bytes": sum(item.size for item in files),
        "file_count": len(files),
        "max_size_bytes": limits.max_size_bytes,
        "auto_delete_enabled": bool(limits.auto_delete_enabled),
    }


def cleanup_image_cache(
    root: Path,
    limits: ImageCacheLimits,
    *,
    protected_paths: Iterable[Path] | None = None,
) -> dict[str, Any]:
    protected = _protected_set(protected_paths)
    removed_files = 0
    removed_expired_files = 0
    removed_oversize_files = 0
    removed_size_bytes = 0

    if limits.auto_delete_enabled:
        cutoff = time.time() - limits.retention_seconds
        for item in sorted(_iter_image_cache_files(root), key=lambda file: file.modified_at):
            try:
                resolved = item.path.resolve()
            except OSError:
                resolved = item.path
            if resolved in protected or item.modified_at >= cutoff:
                continue
            try:
                item.path.unlink()
            except FileNotFoundError:
                continue
            removed_files += 1
            removed_expired_files += 1
            removed_size_bytes += item.size

        files = sorted(_iter_image_cache_files(root), key=lambda file: file.modified_at)
        total_size = sum(item.size for item in files)
        for item in files:
            if total_size <= limits.max_size_bytes:
                break
            try:
                resolved = item.path.resolve()
            except OSError:
                resolved = item.path
            if resolved in protected:
                continue
            try:
                item.path.unlink()
            except FileNotFoundError:
                continue
            total_size -= item.size
            removed_files += 1
            removed_oversize_files += 1
            removed_size_bytes += item.size

        _remove_empty_dirs(root)

    status = get_image_cache_status(root, limits)
    return {
        **status,
        "removed_files": removed_files,
        "removed_expired_files": removed_expired_files,
        "removed_oversize_files": removed_oversize_files,
        "removed_size_bytes": removed_size_bytes,
    }
