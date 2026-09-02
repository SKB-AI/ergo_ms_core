"""Запуск сервисов разработки: Redis → Meilisearch → Jupyter → API/backend → модули → клиент (аналог Start All Services)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PYTHON = sys.executable
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

WARMUP_DONE_ENV = 'ERGO_WARMUP_DONE'


def _run_warmup() -> int:
    warmup = PROJECT_ROOT / 'core' / 'api' / 'scripts' / 'warmup_caches_if_needed.py'
    return subprocess.call([PYTHON, str(warmup)], cwd=str(PROJECT_ROOT))


def _module_start_commands() -> list[tuple[str, str]]:
    from module_tasks_loader import INCLUDE_START_ALL, tasks_for_target

    commands: list[tuple[str, str]] = []
    for entry in tasks_for_target(PROJECT_ROOT, INCLUDE_START_ALL):
        commands.append((entry.label, entry.command))
    return commands


def _ordered_dev_commands() -> list[tuple[str, str]]:
    """Порядок: DB logs → Redis → API / media / celery → модули → client|nginx."""
    from deployment_env import (
        is_jupyter_enabled,
        is_nginx_enabled,
        is_redis_enabled,
        is_search_enabled,
    )
    from start_db_logs_dev import db_service_label

    commands: list[tuple[str, str]] = [
        (db_service_label(), 'ergoms start-db-dev'),
    ]
    if is_redis_enabled():
        commands.append(('Redis', 'ergoms start-redis-dev'))
    if is_search_enabled():
        commands.append(('Meilisearch', 'ergoms start-meilisearch-dev'))
    if is_jupyter_enabled():
        commands.append(('Jupyter', 'ergoms start-jupyter-dev'))
    commands.extend([
        ('API', 'ergoms dev'),
        ('Media API', 'ergoms start-media'),
        ('Worker', 'ergoms start-worker'),
        ('Beat', 'ergoms start-beat'),
    ])
    commands.extend(_module_start_commands())
    if is_nginx_enabled():
        commands.append(('Nginx', 'ergoms start-nginx-dev'))
    else:
        commands.append(('Client', 'ergoms start-client-dev'))
    return commands


def _spawn_infra_first() -> set[str]:
    """БД и Redis поднимаются до warmup, чтобы кэш мог писать в Redis."""
    from deployment_env import is_redis_enabled
    from start_db_logs_dev import db_service_label

    early: list[tuple[str, str]] = [
        (db_service_label(), 'ergoms start-db-dev'),
    ]
    if is_redis_enabled():
        early.append(('Redis', 'ergoms start-redis-dev'))

    remaining_labels = {title for title, _cmd in early}
    if os.name == 'nt':
        for title, command in early:
            safe_title = title.replace('"', "'")
            subprocess.Popen(
                f'start "{safe_title}" cmd /c "{command}"',
                shell=True,
                cwd=str(PROJECT_ROOT),
            )
        time.sleep(0.3)
    else:
        for _title, command in early:
            subprocess.Popen(command, shell=True, cwd=str(PROJECT_ROOT))
        time.sleep(0.3)
    return remaining_labels


def _ordered_after_infra(skip_labels: set[str]) -> list[tuple[str, str]]:
    return [
        (title, command)
        for title, command in _ordered_dev_commands()
        if title not in skip_labels
    ]


def _spawn_windows_rest(skip_labels: set[str]) -> int:
    for title, command in _ordered_after_infra(skip_labels):
        safe_title = title.replace('"', "'")
        # Новый cmd от start наследует env родителя, плюс явный set на случай сброса.
        wrapped = f'set {WARMUP_DONE_ENV}=1&& {command}'
        subprocess.Popen(
            f'start "{safe_title}" cmd /c "{wrapped}"',
            shell=True,
            cwd=str(PROJECT_ROOT),
        )
    return 0


def _spawn_linux_rest(skip_labels: set[str]) -> int:
    processes = []
    for _title, command in _ordered_after_infra(skip_labels):
        processes.append(subprocess.Popen(command, shell=True, cwd=str(PROJECT_ROOT)))
    exit_code = 0
    for process in processes:
        code = process.wait()
        if code != 0:
            exit_code = code
    return exit_code


def main() -> int:
    skip_labels = _spawn_infra_first()
    code = _run_warmup()
    if code != 0:
        return code
    os.environ[WARMUP_DONE_ENV] = '1'
    if os.name == 'nt':
        return _spawn_windows_rest(skip_labels)
    return _spawn_linux_rest(skip_labels)


if __name__ == '__main__':
    raise SystemExit(main())
