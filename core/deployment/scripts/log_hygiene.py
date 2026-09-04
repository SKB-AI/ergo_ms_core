"""Общая гигиена каталога logs/: нумерованные копии, gzip, сводка по диску."""

from __future__ import annotations

import gzip
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LogFileInfo:
    path: Path
    size: int
    mtime: float


def format_bytes(size: int) -> str:
    step = 1024.0
    value = float(size)
    for suffix in ('B', 'K', 'M', 'G', 'T'):
        if value < step or suffix == 'T':
            if suffix == 'B':
                return f'{int(value)}B'
            return f'{value:.1f}{suffix}'
        value /= step
    return f'{size}B'


def backup_plain(path: Path, index: int) -> Path:
    return Path(f'{path}.{index}')


def backup_gz(path: Path, index: int) -> Path:
    return Path(f'{path}.{index}.gz')


def existing_backups(path: Path, index: int) -> list[Path]:
    found: list[Path] = []
    for candidate in (backup_gz(path, index), backup_plain(path, index)):
        if candidate.is_file():
            found.append(candidate)
    return found


def shift_backups(path: Path, backup_count: int) -> None:
    for leftover in existing_backups(path, backup_count):
        leftover.unlink()
    for index in range(backup_count - 1, 0, -1):
        for src in existing_backups(path, index):
            dest = backup_gz(path, index + 1) if src.name.endswith('.gz') else backup_plain(path, index + 1)
            if dest.exists():
                dest.unlink()
            src.rename(dest)


def gzip_replace(src: Path) -> tuple[Path, int, int]:
    """Сжимает файл в ``*.gz`` потоково и удаляет исходник. Возвращает путь, размер до и после."""
    before = src.stat().st_size
    dest = src if src.name.endswith('.gz') else Path(f'{src}.gz')
    if dest == src:
        return dest, before, before
    tmp = dest.with_name(f'{dest.name}.tmp')
    with src.open('rb') as incoming, gzip.open(tmp, 'wb', compresslevel=6) as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    tmp.replace(dest)
    src.unlink()
    return dest, before, dest.stat().st_size


def compress_numbered_backups(path: Path, backup_count: int) -> list[tuple[Path, int, int]]:
    """Gzip всех несжатых ``path.N``. Уже сжатые не трогает."""
    done: list[tuple[Path, int, int]] = []
    for index in range(1, backup_count + 1):
        raw = backup_plain(path, index)
        if not raw.is_file():
            continue
        gz = backup_gz(path, index)
        if gz.exists():
            raw.unlink()
            continue
        done.append(gzip_replace(raw))
    return done


def prune_numbered_backups(
    path: Path,
    backup_count: int,
    retention_days: int,
    *,
    now: float | None = None,
) -> list[Path]:
    """Удаляет копии старше срока и хвост с индексом больше backup_count."""
    removed: list[Path] = []
    cutoff = None
    if retention_days > 0:
        cutoff = (now if now is not None else time.time()) - retention_days * 86400
    scan_upto = max(backup_count + 8, backup_count)
    for index in range(1, scan_upto + 1):
        for candidate in existing_backups(path, index):
            over_count = index > backup_count
            too_old = cutoff is not None and candidate.stat().st_mtime < cutoff
            if over_count or too_old:
                candidate.unlink()
                removed.append(candidate)
    return removed


def list_log_files(logs_dir: Path) -> list[LogFileInfo]:
    if not logs_dir.is_dir():
        return []
    items: list[LogFileInfo] = []
    for entry in logs_dir.rglob('*'):
        if not entry.is_file() or entry.name == '.gitkeep':
            continue
        stat = entry.stat()
        items.append(LogFileInfo(path=entry, size=stat.st_size, mtime=stat.st_mtime))
    items.sort(key=lambda item: (-item.size, str(item.path)))
    return items
