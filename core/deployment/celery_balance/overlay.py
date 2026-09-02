"""Overlay decision.json — не переписывает celery_workers.yaml."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_layout import cache_celery_balance_dir, ensure_dir

from celery_balance.constants import DECISION_FILENAME, MODE_AUTO
from celery_balance.planner import WorkerPlan
from celery_balance.settings import BalanceSettings


@dataclass(frozen=True)
class WorkerOverride:
    concurrency: int
    prefetch_multiplier: int | None
    autoscale_min: int | None
    autoscale_max: int | None


def decision_path(project_root: Path) -> Path:
    return cache_celery_balance_dir(project_root) / DECISION_FILENAME


def write_decision(
    project_root: Path,
    *,
    settings: BalanceSettings,
    budget: dict[str, Any],
    queues: dict[str, Any],
    forecast: dict[str, Any],
    workers: list[WorkerPlan],
    yaml_workers: dict[str, Any],
) -> Path:
    path = decision_path(project_root)
    ensure_dir(path.parent)
    queue_limits: dict[str, int] = {}
    queue_classes: dict[str, str] = {}
    pause_queues: list[str] = []
    reserve_light = 0
    non_light_caps: list[int] = []
    for item in workers:
        pause_queues.extend(item.pause_queues)
        reserve_light = max(reserve_light, int(item.reserve_light or 0))
        if item.non_light_cap > 0:
            non_light_caps.append(int(item.non_light_cap))
        for queue_name, limit in item.queue_limits.items():
            prev = queue_limits.get(queue_name)
            queue_limits[queue_name] = limit if prev is None else min(prev, limit)
        for queue_name, task_class in item.queue_classes.items():
            queue_classes[str(queue_name)] = str(task_class)
    payload = {
        'version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'mode': settings.mode,
        'budget': budget,
        'queues': queues,
        'forecast': forecast,
        'yaml': yaml_workers,
        'plans': {item.name: item.to_dict() for item in workers},
        'queue_limits': queue_limits,
        'queue_classes': queue_classes,
        'reserve_light': reserve_light,
        'non_light_cap': min(non_light_caps) if non_light_caps else 0,
        'pause_queues': sorted(set(pause_queues)),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def load_decision(project_root: Path) -> dict[str, Any] | None:
    path = decision_path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def worker_override(
    project_root: Path,
    worker_name: str | None,
    *,
    mode: str | None = None,
) -> WorkerOverride | None:
    if mode is not None and mode != MODE_AUTO:
        return None
    data = load_decision(project_root)
    if not data:
        return None
    plans = data.get('plans')
    if not isinstance(plans, dict):
        return None
    key = worker_name or 'all'
    raw = plans.get(key)
    if not isinstance(raw, dict) and worker_name is None and len(plans) == 1:
        raw = next(iter(plans.values()), None)
    if not isinstance(raw, dict):
        return None
    try:
        concurrency = int(raw.get('concurrency'))
    except (TypeError, ValueError):
        return None
    if concurrency < 1:
        return None
    prefetch = raw.get('prefetch_multiplier')
    try:
        prefetch_i = int(prefetch) if prefetch is not None else None
    except (TypeError, ValueError):
        prefetch_i = None
    try:
        a_min = int(raw['autoscale_min']) if raw.get('autoscale_min') is not None else None
        a_max = int(raw['autoscale_max']) if raw.get('autoscale_max') is not None else None
    except (TypeError, ValueError):
        a_min, a_max = None, None
    return WorkerOverride(
        concurrency=concurrency,
        prefetch_multiplier=prefetch_i,
        autoscale_min=a_min,
        autoscale_max=a_max,
    )
