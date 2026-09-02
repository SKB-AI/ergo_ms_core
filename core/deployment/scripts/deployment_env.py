"""Чтение переменных развёртывания из .env (+ env/*.env) без Django."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
DEPLOYMENT_NGINX = DEPLOYMENT_DIR / 'nginx'
_HOST_LOOPBACK = '127.0.0.1'

if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from env_file_loader import load_project_env  # noqa: E402
from ergo_modes import (  # noqa: E402
    effective_docker_enabled,
    effective_nginx_enabled,
    effective_postgres_force_install,
    effective_redis_enabled,
    ergo_db,
    should_install_portable_postgres,
)

_MERGED_CACHE: dict[str, str] | None = None


def invalidate_env_cache() -> None:
    global _MERGED_CACHE
    _MERGED_CACHE = None


def _merged_env() -> dict[str, str]:
    global _MERGED_CACHE
    if _MERGED_CACHE is None:
        _MERGED_CACHE = load_project_env(PROJECT_ROOT)
    return _MERGED_CACHE


def _values_for_modes() -> dict[str, str]:
    """os.environ перекрывает файлы для mode-ключей."""
    values = dict(_merged_env())
    for key in (
        'ERGO_RUNTIME',
        'ERGO_PROXY',
        'ERGO_BROKER',
        'ERGO_DB',
        'ERGO_JUPYTER',
        'NGINX_ENABLED',
        'REDIS_ENABLED',
        'DOCKER_ENABLED',
        'POSTGRES_FORCE_INSTALL',
        'DOCKER_PROFILE_POSTGRES',
    ):
        env_val = os.environ.get(key)
        if env_val is not None and str(env_val).strip() != '':
            values[key] = str(env_val).strip()
    return values


def running_in_container() -> bool:
    if Path('/.dockerenv').is_file():
        return True
    cgroup = Path('/proc/self/cgroup')
    if cgroup.is_file():
        try:
            return 'docker' in cgroup.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            pass
    return False


def effective_redis_host() -> str:
    """Хост Redis для portable Redis на хосте (имя compose-сервиса → 127.0.0.1)."""
    host = read_env('REDIS_HOST', _HOST_LOOPBACK).strip() or _HOST_LOOPBACK
    service = read_env('DOCKER_SERVICE_REDIS', 'redis').strip().lower() or 'redis'
    if not running_in_container() and host.lower() in {service, 'redis'}:
        return _HOST_LOOPBACK
    return host


def effective_redis_port() -> int:
    raw = read_env('REDIS_PORT', '6379').strip() or '6379'
    try:
        return int(raw)
    except ValueError:
        return 6379


def read_env(name: str, default: str = '') -> str:
    value = os.environ.get(name)
    if value is not None and str(value).strip() != '':
        return str(value).strip()
    return _merged_env().get(name, default)


def is_nginx_enabled() -> bool:
    return effective_nginx_enabled(_values_for_modes())


def is_redis_enabled() -> bool:
    return effective_redis_enabled(_values_for_modes())


def is_search_enabled() -> bool:
    from ergo_modes import effective_search_enabled  # noqa: WPS433

    return effective_search_enabled(_values_for_modes())


def is_jupyter_enabled() -> bool:
    from ergo_modes import effective_jupyter_enabled  # noqa: WPS433

    return effective_jupyter_enabled(_values_for_modes())


def get_ergo_db() -> str:
    """Режим default БД из ERGO_DB (sqlite|postgres|portable_postgres|mysql|mssql)."""
    return ergo_db(_values_for_modes())


def is_docker_enabled() -> bool:
    return effective_docker_enabled(_values_for_modes())


def _env_truthy(name: str, default: str = 'false') -> bool:
    return read_env(name, default).lower() in ('1', 'true', 'yes', 'on')


def is_portable_python_enabled() -> bool:
    """Ставить portable Python в virtual_env при setup-full (по умолчанию да)."""
    return _env_truthy('PORTABLE_PYTHON_ENABLED', 'true')


def is_portable_nodejs_enabled() -> bool:
    """Ставить portable Node.js в virtual_env при setup-full (по умолчанию да)."""
    return _env_truthy('PORTABLE_NODEJS_ENABLED', 'true')


def resolve_public_host() -> str:
    explicit = read_env('NGINX_PUBLIC_HOST')
    if explicit:
        return explicit

    server_name = read_env('NGINX_SERVER_NAME', 'localhost')
    if server_name not in ('', 'localhost', '127.0.0.1'):
        return server_name

    sys.path.insert(0, str(DEPLOYMENT_NGINX))
    try:
        from detect_lan_ip import detect_lan_ip  # noqa: WPS433
        detected = detect_lan_ip()
        if detected:
            return detected
    finally:
        if str(DEPLOYMENT_NGINX) in sys.path:
            sys.path.remove(str(DEPLOYMENT_NGINX))

    return 'localhost'


def is_postgres_force_install() -> bool:
    """POSTGRES_FORCE_INSTALL или ERGO_DB=portable_postgres."""
    return effective_postgres_force_install(_values_for_modes())


def should_setup_portable_postgres() -> bool:
    return should_install_portable_postgres(_values_for_modes())
