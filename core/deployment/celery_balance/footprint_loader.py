"""Загрузка modules/<имя>/task_footprint.yaml через ModuleCatalog (вне Django)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from lifecycle.modules.catalog import ModuleCatalog  # noqa: E402

from celery_balance.constants import (  # noqa: E402
    CLASS_DEFAULTS,
    CLASS_LIGHT,
    CLASS_MEDIUM,
    DEFAULT_QUEUE_NAME,
    FOOTPRINT_FILENAME,
    TASK_CLASSES,
)


@dataclass(frozen=True)
class TaskFootprint:
    queue: str
    pattern: str
    task_class: str
    ram_mb: float
    cpu_units: float
    gpu_required: bool
    vram_mb: float
    max_parallel: int
    cpu_fallback: bool
    module: str


@dataclass(frozen=True)
class FootprintCatalog:
    tasks: tuple[TaskFootprint, ...]
    by_queue: dict[str, TaskFootprint]


def _warn(message: str) -> None:
    print(format_console('warning', message), file=sys.stderr)


def _as_float(value: Any, default: float) -> float:
    if value is None or value == '':
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    if value is None or value == '':
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _class_defaults(task_class: str) -> dict[str, float | int]:
    return CLASS_DEFAULTS.get(task_class, CLASS_DEFAULTS[CLASS_MEDIUM])


def _parse_task(
    raw: Any,
    *,
    module: str,
    defaults: dict[str, Any],
    path: Path,
) -> TaskFootprint | None:
    if not isinstance(raw, dict):
        _warn(t('celery_balance_footprint_task_not_object', path=path, module=module))
        return None

    task_class = str(raw.get('class') or defaults.get('class') or CLASS_MEDIUM).strip().lower()
    if task_class not in TASK_CLASSES:
        _warn(
            t(
                'celery_balance_footprint_unknown_class',
                path=path,
                module=module,
                task_class=task_class,
            )
        )
        task_class = CLASS_MEDIUM

    base = _class_defaults(task_class)
    queue = str(raw.get('queue') or module).strip()
    if not queue:
        _warn(t('celery_balance_footprint_empty_queue', path=path, module=module))
        return None

    pattern = str(raw.get('pattern') or '').strip()
    ram_mb = _as_float(raw.get('ram_mb', defaults.get('ram_mb')), float(base['ram_mb']))
    cpu_units = _as_float(
        raw.get('cpu_units', defaults.get('cpu_units')),
        float(base['cpu_units']),
    )
    vram_mb = _as_float(raw.get('vram_mb', defaults.get('vram_mb')), float(base['vram_mb']))
    max_parallel = _as_int(
        raw.get('max_parallel', defaults.get('max_parallel')),
        int(base['max_parallel']),
    )
    gpu_required = bool(raw.get('gpu_required', defaults.get('gpu_required', False)))
    if 'cpu_fallback' in raw:
        cpu_fallback = bool(raw.get('cpu_fallback'))
    elif 'cpu_fallback' in defaults:
        cpu_fallback = bool(defaults.get('cpu_fallback'))
    else:
        cpu_fallback = True
    if ram_mb <= 0:
        ram_mb = float(base['ram_mb'])
    if cpu_units <= 0:
        cpu_units = float(base['cpu_units'])

    return TaskFootprint(
        queue=queue,
        pattern=pattern,
        task_class=task_class,
        ram_mb=ram_mb,
        cpu_units=cpu_units,
        gpu_required=gpu_required,
        vram_mb=max(0.0, vram_mb),
        max_parallel=max_parallel,
        cpu_fallback=cpu_fallback,
        module=module,
    )


def _load_file(path: Path, module_hint: str) -> tuple[TaskFootprint, ...]:
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception as exc:  # noqa: BLE001 — битый YAML не роняет CLI
        _warn(t('celery_balance_footprint_invalid', path=path, error=exc))
        return ()

    if not isinstance(data, dict):
        _warn(t('celery_balance_footprint_root_not_object', path=path))
        return ()

    module = str(data.get('module') or module_hint).strip() or module_hint
    defaults = data.get('defaults') if isinstance(data.get('defaults'), dict) else {}
    raw_tasks = data.get('tasks')
    if raw_tasks is None:
        parsed = _parse_task({'queue': module}, module=module, defaults=defaults, path=path)
        return (parsed,) if parsed else ()
    if not isinstance(raw_tasks, list):
        _warn(t('celery_balance_footprint_tasks_not_list', path=path, module=module))
        return ()

    items: list[TaskFootprint] = []
    for entry in raw_tasks:
        parsed = _parse_task(entry, module=module, defaults=defaults, path=path)
        if parsed is not None:
            items.append(parsed)
    return tuple(items)


@lru_cache(maxsize=8)
def load_footprints(project_root: str) -> FootprintCatalog:
    root = Path(project_root).resolve()
    catalog = ModuleCatalog.from_env(root)
    tasks: list[TaskFootprint] = []
    by_queue: dict[str, TaskFootprint] = {}

    for module_dir in catalog.iter_module_dirs():
        path = module_dir / FOOTPRINT_FILENAME
        if not path.is_file():
            continue
        for item in _load_file(path, module_dir.name):
            tasks.append(item)
            if item.queue not in by_queue:
                by_queue[item.queue] = item
            else:
                _warn(
                    t(
                        'celery_balance_footprint_queue_collision',
                        queue=item.queue,
                        module=item.module,
                    )
                )

    return FootprintCatalog(tasks=tuple(tasks), by_queue=by_queue)


def default_footprint(queue: str) -> TaskFootprint:
    task_class = CLASS_LIGHT if queue == DEFAULT_QUEUE_NAME else CLASS_MEDIUM
    base = CLASS_DEFAULTS[task_class]
    return TaskFootprint(
        queue=queue,
        pattern='',
        task_class=task_class,
        ram_mb=float(base['ram_mb']),
        cpu_units=float(base['cpu_units']),
        gpu_required=False,
        vram_mb=0.0,
        max_parallel=int(base['max_parallel']),
        cpu_fallback=True,
        module='',
    )


def resolve_footprint(queue: str, catalog: FootprintCatalog) -> TaskFootprint:
    found = catalog.by_queue.get(queue)
    if found is not None:
        return found
    return default_footprint(queue)
