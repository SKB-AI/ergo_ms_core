"""Чтение переменных логирования из .env + env/*.env (без Django)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from deployment_env import PROJECT_ROOT, read_env

_DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from env_file_loader import load_project_env  # noqa: E402

_MERGED_BY_ROOT: dict[Path, dict[str, str]] = {}

LOG_FILE_DEFAULTS: dict[str, str] = {
    'API': 'api.log',
    'MEDIA_API': 'media_api.log',
    'CELERY': 'celery.log',
    'CELERY_WORKER': 'celery_worker.log',
    'CELERY_BEAT': 'celery_beat.log',
    'CELERY_TASKS': 'celery_tasks.log',
    'CELERY_BROKER': 'celery_broker.log',
    'NGINX_ACCESS': 'nginx-access.log',
    'NGINX_ERROR': 'nginx-error.log',
    'REDIS': 'redis.log',
    'MEILISEARCH': 'meilisearch.log',
    'POSTGRES': 'postgresql.log',
    'CLIENT_DEV': 'client-dev.log',
    'CLIENT_BROWSER': 'client-browser.log',
    'CLIENT_MONITOR': 'client-monitor.log',
    'AUDIT': 'audit.log',
    'OLLAMA': 'ollama-serve.log',
    'JUPYTER': 'jupyter.log',
    'SETUP_FULL': 'setup-full.log',
    'ERGOMS': 'ergoms.log',
}


LOG_LEVEL_FILE_DEFAULTS: dict[str, str] = {
    'CELERY_BROKER': 'INFO',
    'CLIENT_BROWSER': 'WARNING',
    'CLIENT_MONITOR': 'INFO',
    'AUDIT': 'INFO',
}


NGINX_ERROR_LEVELS = frozenset({
    'debug', 'info', 'notice', 'warn', 'warning', 'error', 'crit', 'critical', 'alert', 'emerg',
})
REDIS_LOG_LEVELS = frozenset({'debug', 'verbose', 'notice', 'warning', 'warn'})
VITE_LOG_LEVELS = frozenset({'info', 'warn', 'warning', 'error', 'silent'})


def _merged_env(project_root: Path | None = None) -> dict[str, str]:
    root = (project_root or PROJECT_ROOT).resolve()
    cached = _MERGED_BY_ROOT.get(root)
    if cached is None:
        cached = load_project_env(root)
        _MERGED_BY_ROOT[root] = cached
    return cached


def _read_env_value(name: str, default: str = '', project_root: Path | None = None) -> str:
    """os.environ → merge (.env + env/*.env, в т.ч. logging.env)."""
    root = (project_root or PROJECT_ROOT).resolve()
    if root == PROJECT_ROOT.resolve():
        return read_env(name, default)
    os_val = os.environ.get(name)
    if os_val is not None and str(os_val).strip() != '':
        return str(os_val).strip()
    return _merged_env(root).get(name, default)


def read_bool(name: str, default: bool = True, project_root: Path | None = None) -> bool:
    raw = _read_env_value(name, '', project_root)
    if raw == '':
        return default
    return raw.lower() in ('1', 'true', 'yes', 'on')


def read_int(name: str, default: int, project_root: Path | None = None) -> int:
    raw = _read_env_value(name, '', project_root)
    if raw == '':
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_logs_dir(project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    custom = _read_env_value('ERGO_LOGS_DIR', '', root)
    if custom:
        path = Path(custom)
        if not path.is_absolute():
            path = root / path
        return path
    return root / 'logs'


def log_basename(key: str, project_root: Path | None = None) -> str:
    custom = _read_env_value(f'ERGO_LOG_FILE_{key}', '', project_root)
    if custom:
        name = Path(custom).name
        return name or LOG_FILE_DEFAULTS[key]
    return LOG_FILE_DEFAULTS[key]


def log_file_path(key: str, project_root: Path | None = None) -> Path:
    return resolve_logs_dir(project_root) / log_basename(key, project_root)


def rotation_settings(project_root: Path | None = None) -> dict[str, int]:
    return {
        'max_bytes': read_int('ERGO_LOG_MAX_BYTES', 10 * 1024 * 1024, project_root),
        'backup_count': read_int('ERGO_LOG_BACKUP_COUNT', 5, project_root),
        'broker_max_bytes': read_int('ERGO_LOG_BROKER_MAX_BYTES', 5 * 1024 * 1024, project_root),
        'broker_backup_count': read_int('ERGO_LOG_BROKER_BACKUP_COUNT', 3, project_root),
    }


def file_level_for_key(
    key: str,
    project_root: Path | None = None,
    service_prefix: str | None = None,
) -> str:
    specific = _read_env_value(f'ERGO_LOG_LEVEL_{key}', '', project_root)
    if specific:
        return specific.upper()
    if service_prefix:
        level = _read_env_value(f'{service_prefix}_LOG_FILE_LEVEL', '', project_root)
        if level:
            return level.upper()
    global_level = _read_env_value('ERGO_LOG_FILE_LEVEL', '', project_root)
    if global_level:
        return global_level.upper()
    return LOG_LEVEL_FILE_DEFAULTS.get(key, 'INFO')


def service_levels(service: str, project_root: Path | None = None) -> tuple[str, str, bool]:
    """Уровни только из ERGO_LOG_* / <SERVICE>_LOG_* — без ветвления по *_DEPLOY_TYPE."""
    prefix = service.upper().replace('-', '_')
    default_file = 'INFO'

    file_level = _read_env_value(f'{prefix}_LOG_FILE_LEVEL', '', project_root)
    if not file_level and prefix == 'CELERY_BEAT':
        file_level = _read_env_value('CELERY_LOG_FILE_LEVEL', '', project_root)
    if not file_level:
        file_level = _read_env_value('ERGO_LOG_FILE_LEVEL', default_file, project_root)

    console_level = _read_env_value(f'{prefix}_LOG_CONSOLE_LEVEL', '', project_root)
    if not console_level:
        console_level = _read_env_value('ERGO_LOG_CONSOLE_LEVEL', 'INFO', project_root)

    console_enabled = read_bool('ERGO_LOG_CONSOLE', True, project_root)
    service_console = _read_env_value(f'{prefix}_LOG_CONSOLE', '', project_root)
    if service_console:
        lowered = service_console.lower()
        if lowered in ('0', 'false', 'no', 'off'):
            console_enabled = False
        elif lowered in ('1', 'true', 'yes', 'on'):
            console_enabled = True

    return file_level.upper(), console_level.upper(), console_enabled


def resolve_logging_service(argv: list[str] | None = None) -> str:
    joined = ' '.join(argv or sys.argv).lower()
    if 'start_celery_beat' in joined or ('celery' in joined and 'beat' in joined):
        return 'celery_beat'
    if 'start_celery_worker' in joined or ('celery' in joined and 'worker' in joined):
        return 'celery'
    return 'api'


def nginx_error_log_level(project_root: Path | None = None) -> str:
    raw = (_read_env_value('NGINX_ERROR_LOG_LEVEL', 'warn', project_root) or 'warn').lower()
    if raw == 'warning':
        raw = 'warn'
    if raw == 'critical':
        raw = 'crit'
    return raw if raw in NGINX_ERROR_LEVELS else 'warn'


def nginx_access_log_enabled(project_root: Path | None = None) -> bool:
    return read_bool('NGINX_ACCESS_LOG_ENABLED', True, project_root)


def redis_log_level(project_root: Path | None = None) -> str:
    raw = (_read_env_value('REDIS_LOG_LEVEL', 'notice', project_root) or 'notice').lower()
    if raw == 'warn':
        raw = 'warning'
    return raw if raw in REDIS_LOG_LEVELS else 'notice'


def client_dev_log_enabled(project_root: Path | None = None) -> bool:
    return read_bool('CLIENT_DEV_LOG_ENABLED', True, project_root)


def client_dev_log_level(project_root: Path | None = None) -> str:
    raw = (
        _read_env_value('CLIENT_DEV_LOG_LEVEL', '', project_root)
        or _read_env_value('ERGO_LOG_CONSOLE_LEVEL', 'info', project_root)
    ).lower()
    if raw == 'warning':
        raw = 'warn'
    return raw if raw in VITE_LOG_LEVELS else 'info'


def _read_int_chain(
    names: tuple[str, ...],
    default: int,
    project_root: Path | None = None,
) -> int:
    for name in names:
        raw = _read_env_value(name, '', project_root)
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return default


def _read_interval(project_root: Path | None = None) -> str:
    raw = (_read_env_value('ERGO_LOG_INFRA_ROTATE_INTERVAL', 'hourly', project_root) or 'hourly').strip().lower()
    return raw if raw in ('hourly', 'daily') else 'hourly'


def infra_rotation_settings(project_root: Path | None = None) -> dict[str, int | bool | str]:
    """Параметры ротации nginx/redis/client-dev (ergoms rotate-logs)."""
    return {
        'enabled': read_bool('ERGO_LOG_INFRA_ROTATE_ENABLED', True, project_root),
        'compress': read_bool('ERGO_LOG_COMPRESS', True, project_root),
        'retention_days': _read_int_chain(('ERGO_LOG_RETENTION_DAYS',), 14, project_root),
        'interval': _read_interval(project_root),
        'nginx_max_bytes': _read_int_chain(
            ('ERGO_LOG_NGINX_MAX_BYTES', 'ERGO_LOG_INFRA_MAX_BYTES', 'ERGO_LOG_MAX_BYTES'),
            10 * 1024 * 1024,
            project_root,
        ),
        'nginx_backup_count': _read_int_chain(
            ('ERGO_LOG_NGINX_BACKUP_COUNT', 'ERGO_LOG_INFRA_BACKUP_COUNT', 'ERGO_LOG_BACKUP_COUNT'),
            5,
            project_root,
        ),
        'redis_max_bytes': _read_int_chain(
            ('ERGO_LOG_REDIS_MAX_BYTES', 'ERGO_LOG_INFRA_MAX_BYTES', 'ERGO_LOG_MAX_BYTES'),
            10 * 1024 * 1024,
            project_root,
        ),
        'redis_backup_count': _read_int_chain(
            ('ERGO_LOG_REDIS_BACKUP_COUNT', 'ERGO_LOG_INFRA_BACKUP_COUNT', 'ERGO_LOG_BACKUP_COUNT'),
            5,
            project_root,
        ),
        'client_dev_max_bytes': _read_int_chain(
            ('ERGO_LOG_CLIENT_DEV_MAX_BYTES', 'ERGO_LOG_INFRA_MAX_BYTES', 'ERGO_LOG_MAX_BYTES'),
            10 * 1024 * 1024,
            project_root,
        ),
        'client_dev_backup_count': _read_int_chain(
            ('ERGO_LOG_CLIENT_DEV_BACKUP_COUNT', 'ERGO_LOG_INFRA_BACKUP_COUNT', 'ERGO_LOG_BACKUP_COUNT'),
            5,
            project_root,
        ),
        'copytruncate_max_bytes': _read_int_chain(
            ('ERGO_LOG_INFRA_MAX_BYTES', 'ERGO_LOG_MAX_BYTES'),
            10 * 1024 * 1024,
            project_root,
        ),
        'copytruncate_backup_count': _read_int_chain(
            ('ERGO_LOG_INFRA_BACKUP_COUNT', 'ERGO_LOG_BACKUP_COUNT'),
            5,
            project_root,
        ),
        'schedule_hour': _read_int_chain(('ERGO_LOG_INFRA_ROTATE_HOUR',), 3, project_root),
    }


def service_log_map(project_root: Path | None = None) -> dict[str, list[str]]:
    return {
        'ergo_ms_api_dev': [log_basename('API', project_root)],
        'ergo_ms_media_api': [log_basename('MEDIA_API', project_root)],
        'ergo_ms_celery_beat': [log_basename('CELERY_BEAT', project_root)],
        'ergo_ms_celery_worker': [log_basename('CELERY_WORKER', project_root)],
        'ergo_ms_client_dev': [log_basename('CLIENT_DEV', project_root)],
        'ergo_ms_nginx': [
            log_basename('NGINX_ERROR', project_root),
            log_basename('NGINX_ACCESS', project_root),
        ],
        'ergo_ms_redis': [log_basename('REDIS', project_root)],
        'ergo_ms_meilisearch': [log_basename('MEILISEARCH', project_root)],
        'ergo-ollama': [log_basename('OLLAMA', project_root)],
        'ergo_ms_ollama': [log_basename('OLLAMA', project_root)],
        'ergo_ms_jupyter': [log_basename('JUPYTER', project_root)],
        # legacy имена до унификации префикса ergo_ms_
        'ergo-api-dev': [log_basename('API', project_root)],
        'ergo-media-api': [log_basename('MEDIA_API', project_root)],
        'ergo-celery-beat': [log_basename('CELERY_BEAT', project_root)],
        'ergo-celery-worker': [log_basename('CELERY_WORKER', project_root)],
        'ergo-client-dev': [log_basename('CLIENT_DEV', project_root)],
        'ergo-redis': [log_basename('REDIS', project_root)],
        'setup-full': [log_basename('SETUP_FULL', project_root)],
        'setup': [log_basename('SETUP_FULL', project_root)],
        'ergoms': [log_basename('ERGOMS', project_root)],
    }


def _cli_main() -> int:
    if len(sys.argv) < 2:
        print('использование: log_env.py <command> [args]', file=sys.stderr)
        return 1

    command = sys.argv[1]
    root = Path(sys.argv[-1]) if len(sys.argv) >= 3 and Path(sys.argv[-1]).exists() else PROJECT_ROOT

    if command == 'path' and len(sys.argv) >= 3:
        key = sys.argv[2]
        pr = Path(sys.argv[3]) if len(sys.argv) >= 4 else PROJECT_ROOT
        print(log_file_path(key, pr), end='')
        return 0
    if command == 'basename' and len(sys.argv) >= 3:
        key = sys.argv[2]
        pr = Path(sys.argv[3]) if len(sys.argv) >= 4 else PROJECT_ROOT
        print(log_basename(key, pr), end='')
        return 0
    if command == 'logs-dir':
        pr = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        print(resolve_logs_dir(pr), end='')
        return 0
    if command == 'nginx-error-level':
        pr = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        print(nginx_error_log_level(pr), end='')
        return 0
    if command == 'nginx-access-enabled':
        pr = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        print('true' if nginx_access_log_enabled(pr) else 'false', end='')
        return 0
    if command == 'redis-log-level':
        pr = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        print(redis_log_level(pr), end='')
        return 0
    if command == 'client-dev-enabled':
        pr = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        print('true' if client_dev_log_enabled(pr) else 'false', end='')
        return 0
    if command == 'client-dev-level':
        pr = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        print(client_dev_log_level(pr), end='')
        return 0
    if command == 'service-levels' and len(sys.argv) >= 3:
        service = sys.argv[2]
        pr = Path(sys.argv[3]) if len(sys.argv) >= 4 else PROJECT_ROOT
        file_level, console_level, console_on = service_levels(service, pr)
        print(f'{file_level}\t{console_level}\t{int(console_on)}', end='')
        return 0
    if command == 'rotation':
        pr = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        rot = rotation_settings(pr)
        print(
            f"{rot['max_bytes']}\t{rot['backup_count']}\t{rot['broker_max_bytes']}\t{rot['broker_backup_count']}",
            end='',
        )
        return 0
    if command == 'infra-rotation':
        pr = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        infra = infra_rotation_settings(pr)
        print(
            f"{int(infra['enabled'])}\t{infra['nginx_max_bytes']}\t{infra['nginx_backup_count']}\t"
            f"{infra['redis_max_bytes']}\t{infra['redis_backup_count']}\t{infra['schedule_hour']}\t"
            f"{int(infra['compress'])}\t{infra['retention_days']}\t{infra['interval']}",
            end='',
        )
        return 0

    print(f'Неизвестная команда: {command}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(_cli_main())
