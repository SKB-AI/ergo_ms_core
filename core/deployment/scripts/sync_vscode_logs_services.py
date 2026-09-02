"""Список сервисов для multi-terminal VS Code (JSON в stdout, без файлов в .vscode/)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from deployment_env import (
    PROJECT_ROOT,
    get_ergo_db,
    is_jupyter_enabled,
    is_nginx_enabled,
    is_redis_enabled,
    is_search_enabled,
)

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cli_locale import t  # noqa: E402
from module_tasks_loader import (  # noqa: E402
    INCLUDE_LOGS_ALL,
    INCLUDE_START_ALL,
    tasks_for_target,
)
from service_names import celery_worker  # noqa: E402

# Имена target для extension provider: ergo-sync
TARGET_LOGS = 'logs'
TARGET_LOGS_ALL = 'logs-all'
TARGET_OPTIONAL = 'optional-services'
TARGET_REDIS_DEV = 'redis-dev'
TARGET_MEILISEARCH_DEV = 'meilisearch-dev'
TARGET_JUPYTER_DEV = 'jupyter-dev'
TARGET_DB_DEV = 'db-dev'
TARGET_CLIENT_DEV = 'client-dev'
TARGET_MODULE_START = 'module-start'
TARGET_MODULE_LOGS = 'module-logs'

ALL_TARGETS = (
    TARGET_LOGS,
    TARGET_LOGS_ALL,
    TARGET_OPTIONAL,
    TARGET_REDIS_DEV,
    TARGET_MEILISEARCH_DEV,
    TARGET_JUPYTER_DEV,
    TARGET_DB_DEV,
    TARGET_CLIENT_DEV,
    TARGET_MODULE_START,
    TARGET_MODULE_LOGS,
)


def _svc(
    key: str,
    description: str,
    *,
    command: str = '',
    stop_command: str = '',
) -> dict[str, str]:
    item: dict[str, str] = {'key': key, 'description': description}
    if command:
        item['command'] = command
    if stop_command:
        item['stop_command'] = stop_command
    return item


def _core_log_services(*, with_commands: bool) -> list[dict[str, str]]:
    items: list[dict[str, str]] = [
        _svc(
            'ergo_ms_api_dev',
            'Django API server',
            command='ergoms logs ergo_ms_api_dev 500' if with_commands else '',
        ),
    ]
    if is_nginx_enabled():
        items.append(
            _svc(
                'ergo_ms_nginx',
                'Nginx reverse proxy',
                command='ergoms logs ergo_ms_nginx 500' if with_commands else '',
            )
        )
    else:
        items.append(
            _svc(
                'ergo_ms_client_dev',
                'Vue.js client dev server',
                command='ergoms logs ergo_ms_client_dev 500' if with_commands else '',
            )
        )
    items.append(
        _svc(
            'ergo_ms_media_api',
            'Media API (CDN / file server)',
            command='ergoms logs ergo_ms_media_api 500' if with_commands else '',
        )
    )
    items.append(
        _svc(
            'ergo_ms_celery_beat',
            'Celery Beat scheduler',
            command='ergoms logs ergo_ms_celery_beat 500' if with_commands else '',
        )
    )
    if is_redis_enabled():
        items.append(
            _svc(
                'ergo_ms_redis',
                'Redis',
                command='ergoms logs ergo_ms_redis 500' if with_commands else '',
            )
        )
    if is_search_enabled():
        items.append(
            _svc(
                'ergo_ms_meilisearch',
                'Meilisearch',
                command='ergoms logs ergo_ms_meilisearch 500' if with_commands else '',
            )
        )
    db_mode = get_ergo_db()
    from start_db_logs_dev import db_service_label, db_terminal_key  # noqa: WPS433

    db_key = db_terminal_key(db_mode)
    items.append(
        _svc(
            f'ergo_ms_{db_key}',
            db_service_label(db_mode),
            command='ergoms start-db-dev' if with_commands else '',
        )
    )
    return items


def _celery_worker_keys() -> list[str]:
    path = PROJECT_ROOT / 'celery_workers.yaml'
    if not path.is_file():
        return ['all']
    try:
        import yaml
    except ImportError:
        return ['all']
    try:
        data: Any = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError):
        return ['all']
    workers = data.get('workers') or {}
    if not isinstance(workers, dict) or not workers:
        return ['all']
    return [str(key) for key in workers]


def _module_services(include_target: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for entry in tasks_for_target(PROJECT_ROOT, include_target):
        key = entry.service_key
        if key in seen_keys:
            key = f'{entry.module}_{key}'
        seen_keys.add(key)
        desc = entry.label.replace('"', "'")
        items.append(
            _svc(
                key,
                desc,
                command=entry.command,
                stop_command=entry.stop_command or '',
            )
        )
    return items


def build_logs_services() -> list[dict[str, str]]:
    return _core_log_services(with_commands=False)


def build_logs_all_services() -> list[dict[str, str]]:
    items = list(_core_log_services(with_commands=True))
    for worker_key in _celery_worker_keys():
        unit = celery_worker(worker_key)
        items.append(
            _svc(
                unit,
                f'Celery Worker-{worker_key}',
                command=f'ergoms logs {unit} 500',
            )
        )
    items.extend(_module_services(INCLUDE_LOGS_ALL))
    return items


def build_optional_services() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if is_redis_enabled():
        items.append(
            _svc(
                'Redis',
                'Redis',
                command='ergoms start-redis-dev',
                stop_command='ergoms stop-redis-dev',
            )
        )
    if is_search_enabled():
        items.append(
            _svc(
                'Meilisearch',
                'Meilisearch',
                command='ergoms start-meilisearch-dev',
                stop_command='ergoms stop-meilisearch-dev',
            )
        )
    if is_jupyter_enabled():
        items.append(
            _svc(
                'Jupyter',
                'Jupyter',
                command='ergoms start-jupyter-dev',
                stop_command='ergoms stop-jupyter-dev',
            )
        )
    if is_nginx_enabled():
        items.append(
            _svc(
                'Nginx',
                'Nginx',
                command='ergoms start-nginx-dev',
                stop_command='ergoms stop-nginx-dev',
            )
        )
    else:
        items.append(
            _svc(
                'Client',
                'Client',
                command='ergoms start-client-dev',
                stop_command='ergoms stop-client-dev',
            )
        )
    return items


def build_redis_dev_services() -> list[dict[str, str]]:
    if is_redis_enabled():
        return [
            _svc(
                'Redis',
                'Redis',
                command='ergoms start-redis-dev',
                stop_command='ergoms stop-redis-dev',
            )
        ]
    return []


def build_meilisearch_dev_services() -> list[dict[str, str]]:
    if is_search_enabled():
        return [
            _svc(
                'Meilisearch',
                'Meilisearch',
                command='ergoms start-meilisearch-dev',
                stop_command='ergoms stop-meilisearch-dev',
            )
        ]
    return []


def build_jupyter_dev_services() -> list[dict[str, str]]:
    if is_jupyter_enabled():
        return [
            _svc(
                'Jupyter',
                'Jupyter',
                command='ergoms start-jupyter-dev',
                stop_command='ergoms stop-jupyter-dev',
            )
        ]
    return []


def build_db_dev_services() -> list[dict[str, str]]:
    from start_db_logs_dev import db_service_label, db_terminal_title  # noqa: WPS433

    db_mode = get_ergo_db()
    title = db_terminal_title(db_mode)
    return [
        _svc(
            title,
            db_service_label(db_mode),
            command='ergoms start-db-dev',
        )
    ]


def build_client_dev_services() -> list[dict[str, str]]:
    if is_nginx_enabled():
        return [
            _svc(
                'Nginx',
                'Nginx',
                command='ergoms start-nginx-dev',
                stop_command='ergoms stop-nginx-dev',
            )
        ]
    return [
        _svc(
            'Client',
            'Client',
            command='ergoms start-client-dev',
            stop_command='ergoms stop-client-dev',
        )
    ]


def build_module_start_services() -> list[dict[str, str]]:
    return _module_services(INCLUDE_START_ALL)


def build_module_logs_services() -> list[dict[str, str]]:
    return _module_services(INCLUDE_LOGS_ALL)


_BUILDERS = {
    TARGET_LOGS: build_logs_services,
    TARGET_LOGS_ALL: build_logs_all_services,
    TARGET_OPTIONAL: build_optional_services,
    TARGET_REDIS_DEV: build_redis_dev_services,
    TARGET_MEILISEARCH_DEV: build_meilisearch_dev_services,
    TARGET_JUPYTER_DEV: build_jupyter_dev_services,
    TARGET_DB_DEV: build_db_dev_services,
    TARGET_CLIENT_DEV: build_client_dev_services,
    TARGET_MODULE_START: build_module_start_services,
    TARGET_MODULE_LOGS: build_module_logs_services,
}


def build_target_payload(target: str) -> dict[str, Any]:
    builder = _BUILDERS.get(target)
    if builder is None:
        raise ValueError(f'unknown target: {target}')
    return {'target': target, 'services': builder()}


def build_all_payloads() -> dict[str, Any]:
    return {name: build_target_payload(name) for name in ALL_TARGETS}


def _print_summary() -> None:
    mode = 'nginx' if is_nginx_enabled() else 'client'
    redis = 'redis' if is_redis_enabled() else 'no-redis'
    db = get_ergo_db()
    for name in ALL_TARGETS:
        payload = build_target_payload(name)
        count = len(payload['services'])
        print(
            t(
                'vscode_sync_target',
                target=name,
                count=count,
                mode=mode,
                redis=redis,
                db=db,
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Service lists for VS Code multi-terminal (JSON stdout).',
    )
    parser.add_argument(
        '--json',
        metavar='TARGET',
        choices=ALL_TARGETS,
        help='Print one target as JSON to stdout',
    )
    parser.add_argument(
        '--json-all',
        action='store_true',
        help='Print all targets as JSON to stdout',
    )
    args = parser.parse_args(argv)

    if args.json_all:
        print(json.dumps(build_all_payloads(), ensure_ascii=False))
        return 0
    if args.json:
        print(json.dumps(build_target_payload(args.json), ensure_ascii=False))
        return 0

    _print_summary()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
