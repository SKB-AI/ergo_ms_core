"""
Ожидание postgres и redis перед стартом сервиса в Docker.

Переменные: ERGO_DOCKER_DB_HOST, ERGO_DOCKER_DB_PORT, REDIS_HOST, REDIS_PORT,
DOCKER_DATABASE, DOCKER_ENABLED, ERGO_DOCKER_SKIP_INFRA_WAIT.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path


def _deployment_dir() -> Path:
    """Каталог core/deployment: bind-mount /app или исходный путь в дереве проекта."""
    mounted = Path('/app/core/deployment')
    if (mounted / 'cli_locale.py').is_file():
        return mounted
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / 'cli_locale.py').is_file():
            return parent
    return mounted


_DEPLOYMENT_DIR = _deployment_dir()
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402


def _env(name: str, default: str = '') -> str:
    return os.environ.get(name, default).strip() or default


def _truthy(name: str, default: bool = False) -> bool:
    value = _env(name, 'true' if default else 'false').lower()
    return value in ('1', 'true', 'yes', 'on')


def wait_tcp(host: str, port: int, timeout: float, label: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(t('wait_service_ok', label=label, host=host, port=port))
                return True
        except OSError:
            time.sleep(1)
    print(t('wait_service_timeout', label=label, host=host, port=port), file=sys.stderr)
    return False


def main() -> int:
    if not _truthy('DOCKER_ENABLED'):
        return 0
    # compose run --no-deps (python-install) не поднимает postgres/redis.
    if _truthy('ERGO_DOCKER_SKIP_INFRA_WAIT'):
        return 0

    timeout = float(_env('ERGO_DOCKER_WAIT_TIMEOUT', '120'))

    broker = _env('ERGO_BROKER', 'redis').lower()
    redis_on = _truthy('REDIS_ENABLED', default=(broker != 'local'))
    if redis_on:
        redis_host = _env('REDIS_HOST', 'redis')
        redis_port = int(_env('REDIS_PORT', '6379') or '6379')
        if not wait_tcp(redis_host, redis_port, timeout, 'Redis'):
            return 1

    db_mode = _env('DOCKER_DATABASE', 'container').lower()
    if db_mode == 'container' and _truthy('DOCKER_PROFILE_POSTGRES', default=True):
        db_host = _env('ERGO_DOCKER_DB_HOST', 'postgres')
        db_port = int(_env('ERGO_DOCKER_DB_PORT', '5432') or '5432')
        if not wait_tcp(db_host, db_port, timeout, 'PostgreSQL'):
            return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
