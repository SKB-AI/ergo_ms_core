"""Краткосрочный прогноз: глубина очереди × медиана wall-time + beat."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_layout import cache_dir

from celery_balance.constants import BEAT_SCHEDULE_CACHE_NAME, SIGNATURE_SEPARATOR
from celery_balance.history import HistoryStats
from celery_balance.queues import QueueSnapshot


@dataclass(frozen=True)
class Forecast:
    pending_sec: float
    beat_entries: int
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            'pending_sec': self.pending_sec,
            'beat_entries': self.beat_entries,
            'notes': list(self.notes),
        }


def _read_bin_payload(path: Path) -> Any | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if SIGNATURE_SEPARATOR in raw:
        payload = raw.split(SIGNATURE_SEPARATOR, 1)[0]
    else:
        payload = raw
    try:
        return json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def load_beat_entry_count(project_root: Path) -> int:
    data = _read_bin_payload(cache_dir(project_root) / BEAT_SCHEDULE_CACHE_NAME)
    if not isinstance(data, dict):
        return 0
    schedule = data.get('schedule')
    if isinstance(schedule, dict):
        return len(schedule)
    if isinstance(schedule, list):
        return len(schedule)
    return 0


def build_forecast(
    queues: tuple[QueueSnapshot, ...],
    history: dict[str, HistoryStats],
    project_root: Path,
) -> Forecast:
    pending = 0.0
    notes: list[str] = []
    for item in queues:
        if item.depth is None or item.depth <= 0:
            continue
        stats = history.get(item.name)
        wall_ms = stats.wall_ms_median if stats is not None else None
        if wall_ms is None:
            notes.append(f'{item.name}: depth={item.depth}, no wall-time history')
            continue
        pending += item.depth * (wall_ms / 1000.0)
        notes.append(f'{item.name}: depth={item.depth} × {wall_ms:.0f} ms')

    beat_entries = load_beat_entry_count(project_root)
    if beat_entries:
        notes.append(f'beat_entries={beat_entries}')
    return Forecast(
        pending_sec=round(pending, 1),
        beat_entries=beat_entries,
        notes=tuple(notes),
    )
