"""История footprint: jsonl без payload задач."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_layout import cache_celery_balance_dir, ensure_dir

from celery_balance.constants import DEFAULT_HISTORY_MIN_SAMPLES, HISTORY_FILENAME


@dataclass(frozen=True)
class HistoryStats:
    samples: int
    ram_mb_median: float | None
    wall_ms_median: float | None
    vram_mb_median: float | None = None


def history_path(project_root: Path) -> Path:
    return cache_celery_balance_dir(project_root) / HISTORY_FILENAME


def append_sample(
    project_root: Path,
    *,
    queue: str,
    task_name: str,
    wall_ms: float,
    peak_rss_mb: float,
    peak_vram_mb: float | None = None,
) -> None:
    path = history_path(project_root)
    ensure_dir(path.parent)
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'queue': queue,
        'task_name': task_name,
        'wall_ms': round(float(wall_ms), 1),
        'peak_rss_mb': round(float(peak_rss_mb), 1),
    }
    if peak_vram_mb is not None:
        record['peak_vram_mb'] = round(float(peak_vram_mb), 1)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def stats_by_queue(
    project_root: Path,
    *,
    min_samples: int = DEFAULT_HISTORY_MIN_SAMPLES,
) -> dict[str, HistoryStats]:
    rows = _load_rows(history_path(project_root))
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        queue = str(row.get('queue') or '').strip()
        if not queue:
            continue
        buckets.setdefault(queue, []).append(row)

    result: dict[str, HistoryStats] = {}
    for queue, items in buckets.items():
        ram_values = [
            float(item['peak_rss_mb'])
            for item in items
            if isinstance(item.get('peak_rss_mb'), (int, float))
        ]
        wall_values = [
            float(item['wall_ms'])
            for item in items
            if isinstance(item.get('wall_ms'), (int, float))
        ]
        vram_values = [
            float(item['peak_vram_mb'])
            for item in items
            if isinstance(item.get('peak_vram_mb'), (int, float))
        ]
        result[queue] = HistoryStats(
            samples=len(items),
            ram_mb_median=(
                round(statistics.median(ram_values), 1)
                if len(ram_values) >= min_samples
                else None
            ),
            wall_ms_median=(
                round(statistics.median(wall_values), 1)
                if len(wall_values) >= min_samples
                else None
            ),
            vram_mb_median=(
                round(statistics.median(vram_values), 1)
                if len(vram_values) >= min_samples
                else None
            ),
        )
    return result
