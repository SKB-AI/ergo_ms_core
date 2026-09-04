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
from logs_paths import parse_module_process_unit  # noqa: E402
from service_names import names_from_root  # noqa: E402

_SHARED_PROCESS_DESCRIPTIONS = {
    'api': 'Shared microservice API',
    'worker': 'Shared Celery Worker',
    'beat': 'Shared Celery Beat',
}

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


def _resolve_profile(project_root: Path | None = None):
    from lifecycle.host_profile import resolve_host_profile_from_root  # noqa: WPS433

    return resolve_host_profile_from_root(project_root or PROJECT_ROOT)


def _core_log_services(
    *,
    with_commands: bool,
    project_root: Path | None = None,
) -> list[dict[str, str]]:
    from lifecycle.host_profile import (  # noqa: WPS433
        SERVICE_API,
        SERVICE_BEAT,
        SERVICE_CLIENT,
        SERVICE_MEDIA,
    )

    root = project_root or PROJECT_ROOT
    profile = _resolve_profile(root)
    names = names_from_root(root)
    items: list[dict[str, str]] = []
    if profile.wants(SERVICE_API):
        items.append(
            _svc(
                names.api_dev,
                'Django API server',
                command=f'ergoms logs {names.api_dev} 500' if with_commands else '',
            )
        )
    if is_nginx_enabled():
        items.append(
            _svc(
                names.nginx,
                'Nginx reverse proxy',
                command=f'ergoms logs {names.nginx} 500' if with_commands else '',
            )
        )
    elif profile.wants(SERVICE_CLIENT):
        items.append(
            _svc(
                names.client_dev,
                'Vue.js client dev server',
                command=f'ergoms logs {names.client_dev} 500' if with_commands else '',
            )
        )
    if profile.wants(SERVICE_MEDIA):
        items.append(
            _svc(
                names.media_api,
                'Media API (CDN / file server)',
                command=f'ergoms logs {names.media_api} 500' if with_commands else '',
            )
        )
    if profile.wants(SERVICE_BEAT):
        items.append(
            _svc(
                names.celery_beat,
                'Celery Beat scheduler',
                command=f'ergoms logs {names.celery_beat} 500' if with_commands else '',
            )
        )
    if is_redis_enabled():
        items.append(
            _svc(
                names.redis,
                'Redis',
                command=f'ergoms logs {names.redis} 500' if with_commands else '',
            )
        )
    if is_search_enabled():
        items.append(
            _svc(
                names.meilisearch,
                'Meilisearch',
                command=f'ergoms logs {names.meilisearch} 500' if with_commands else '',
            )
        )
    db_mode = get_ergo_db()
    from start_db_logs_dev import db_service_label, db_terminal_key  # noqa: WPS433

    db_key = db_terminal_key(db_mode)
    items.append(
        _svc(
            f'{names.prefix}_{db_key}',
            db_service_label(db_mode),
            command='ergoms start-db-dev' if with_commands else '',
        )
    )
    return items


def _celery_worker_keys(project_root: Path | None = None) -> list[str]:
    path = (project_root or PROJECT_ROOT) / 'celery_workers.yaml'
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


def _module_process_log_kinds(project_root: Path | None = None) -> set[str]:
    """Какие из api/worker/beat есть у микросервисов (host_lifecycle + профиль)."""
    from env_file_loader import load_project_env  # noqa: WPS433
    from host_lifecycle_loader import (  # noqa: WPS433
        allows_module_kind,
        load_host_lifecycle_entries,
    )
    from lifecycle.host_profile import resolve_host_profile  # noqa: WPS433
    from lifecycle.modules.catalog import ModuleCatalog  # noqa: WPS433

    root = (project_root or PROJECT_ROOT).resolve()
    file_env = load_project_env(root)
    catalog = ModuleCatalog.from_env(root, file_env)
    host_profile = resolve_host_profile(file_env)
    kinds: set[str] = set()
    for entry in load_host_lifecycle_entries(str(root)):
        for unit in entry.service_units:
            parsed = parse_module_process_unit(unit)
            if parsed is None:
                continue
            _module_name, kind = parsed
            if kind not in _SHARED_PROCESS_DESCRIPTIONS:
                continue
            if not allows_module_kind(catalog, host_profile, entry.module, kind):
                continue
            kinds.add(kind)
    return kinds


def _shared_log_key(kind: str, names) -> str:
    if kind == 'api':
        return names.api_dev
    if kind == 'worker':
        return names.celery_worker_base
    if kind == 'beat':
        return names.celery_beat
    raise ValueError(f'unknown shared log kind: {kind}')


def _shared_microservice_log_services(
    project_root: Path | None = None,
    *,
    with_commands: bool = True,
    existing_keys: set[str] | None = None,
) -> list[dict[str, str]]:
    """Три общих хвоста, не отдельный терминал на каждый модуль."""
    root = project_root or PROJECT_ROOT
    names = names_from_root(root)
    skip = set(existing_keys or ())
    kinds = _module_process_log_kinds(root)
    items: list[dict[str, str]] = []
    for kind in ('api', 'worker', 'beat'):
        if kind not in kinds:
            continue
        key = _shared_log_key(kind, names)
        if key in skip:
            continue
        if kind == 'worker' and any(names.is_celery_worker(item) for item in skip):
            continue
        items.append(
            _svc(
                key,
                _SHARED_PROCESS_DESCRIPTIONS[kind],
                command=f'ergoms logs {key} 500' if with_commands else '',
            )
        )
        skip.add(key)
    return items


def _module_services(
    include_target: str,
    project_root: Path | None = None,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for entry in tasks_for_target(project_root or PROJECT_ROOT, include_target):
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


def build_logs_services(project_root: Path | None = None) -> list[dict[str, str]]:
    root = project_root or PROJECT_ROOT
    items = list(_core_log_services(with_commands=False, project_root=root))
    items.extend(
        _shared_microservice_log_services(
            root,
            with_commands=False,
            existing_keys={item['key'] for item in items},
        )
    )
    return items


def build_logs_all_services(project_root: Path | None = None) -> list[dict[str, str]]:
    from lifecycle.host_profile import SERVICE_YAML_WORKERS  # noqa: WPS433

    root = project_root or PROJECT_ROOT
    profile = _resolve_profile(root)
    items = list(_core_log_services(with_commands=True, project_root=root))
    if profile.wants(SERVICE_YAML_WORKERS):
        names = names_from_root(root)
        for worker_key in _celery_worker_keys(root):
            unit = names.celery_worker(worker_key)
            items.append(
                _svc(
                    unit,
                    f'Celery Worker-{worker_key}',
                    command=f'ergoms logs {unit} 500',
                )
            )
    items.extend(
        _shared_microservice_log_services(
            root,
            with_commands=True,
            existing_keys={item['key'] for item in items},
        )
    )
    items.extend(_module_services(INCLUDE_LOGS_ALL, root))
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
