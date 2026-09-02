"""Постоянный кэш скачанных архивов в virtual_env/cache/downloads."""

from __future__ import annotations

import shutil
from pathlib import Path

from project_layout import cache_downloads_dir, ensure_dir

MIN_CACHED_ARCHIVE_BYTES = 1024


def cached_archive_path(root: Path, package_name: str, filename: str) -> Path:
    """Путь к архиву в кэше: downloads/{package}-{filename}."""
    safe_name = filename.replace('/', '_').replace('\\', '_')
    return ensure_dir(cache_downloads_dir(root)) / f'{package_name}-{safe_name}'


def copy_cached_archive(cache_path: Path, destination: Path) -> bool:
    """Копирует кэш в destination. True — файл уже был в кэше."""
    try:
        if not cache_path.is_file() or cache_path.stat().st_size < MIN_CACHED_ARCHIVE_BYTES:
            return False
    except OSError:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.resolve() != destination.resolve():
        shutil.copy2(cache_path, destination)
    return True


def remember_downloaded_archive(cache_path: Path, source: Path) -> None:
    """Сохраняет скачанный файл в кэш для следующих установок."""
    try:
        if not source.is_file() or source.stat().st_size < MIN_CACHED_ARCHIVE_BYTES:
            return
    except OSError:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.resolve() != source.resolve():
        shutil.copy2(source, cache_path)


def download_with_cache(
    root: Path,
    package_name: str,
    destination: Path,
    download_fn,
    *,
    filename: str | None = None,
) -> bool:
    """
    Берёт файл из кэша или вызывает download_fn(destination) и запоминает результат.

    Возвращает True, если обошлись кэшем без сети.
    """
    cache_path = cached_archive_path(root, package_name, filename or destination.name)
    if copy_cached_archive(cache_path, destination):
        return True
    download_fn(destination)
    remember_downloaded_archive(cache_path, destination)
    return False
