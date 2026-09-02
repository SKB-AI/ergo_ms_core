"""
Ортогональные режимы ERGO_* и effective-флаги (stdlib).

Явные REDIS_ENABLED / NGINX_ENABLED / DOCKER_ENABLED / POSTGRES_FORCE_INSTALL
имеют приоритет над ERGO_*, если заданы в values.
"""

from __future__ import annotations

from typing import Mapping

ERGO_RUNTIME_VALUES = frozenset({'host', 'docker'})
ERGO_PROXY_VALUES = frozenset({'none', 'nginx'})
ERGO_BROKER_VALUES = frozenset({'local', 'redis'})
ERGO_DB_VALUES = frozenset({
    'sqlite',
    'postgres',
    'portable_postgres',
    'mysql',
    'mssql',
})
ERGO_JUPYTER_VALUES = frozenset({'none', 'auto', 'local', 'lan', 'nginx'})
ERGO_EMAIL_VALUES = frozenset({'none', 'smtp'})
ERGO_MEDIA_VALUES = frozenset({'local', 'remote'})
ERGO_ENV_VALUES = frozenset({'development', 'production'})
ERGO_REALTIME_VALUES = frozenset({'websocket', 'sse', 'http_polling'})
ERGO_SECURITY_VALUES = frozenset({'open', 'standard', 'hardened', 'maximum'})
ERGO_SECURITY_ENFORCE_VALUES = frozenset({'off', 'warn', 'raise'})
_ERGO_SECURITY_RANKS = {
    'open': 0,
    'standard': 1,
    'hardened': 2,
    'maximum': 3,
}
_ERGO_ENV_ALIASES = {
    'dev': 'development',
    'prod': 'production',
}

_ERGO_DB_TO_ENGINE = {
    'sqlite': 'sqlite',
    'postgres': 'postgresql',
    'portable_postgres': 'postgresql',
    'mysql': 'mysql',
    'mssql': 'mssql',
}


def _get(values: Mapping[str, str], key: str, default: str = '') -> str:
    raw = values.get(key, default)
    if raw is None:
        return default
    return str(raw).strip()


def env_bool(value: str | None, *, default: bool = False) -> bool:
    """Истина для 1/true/yes/on; пустое значение → default."""
    if value is None or str(value).strip() == '':
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def env_bool_key(values: Mapping[str, str], key: str, *, default: bool = False) -> bool:
    return env_bool(values.get(key), default=default)


def _has_explicit(values: Mapping[str, str], key: str) -> bool:
    return key in values and str(values.get(key, '')).strip() != ''


def ergo_runtime(values: Mapping[str, str]) -> str:
    value = _get(values, 'ERGO_RUNTIME', 'host').lower()
    return value if value in ERGO_RUNTIME_VALUES else 'host'


def ergo_proxy(values: Mapping[str, str]) -> str:
    value = _get(values, 'ERGO_PROXY', 'none').lower()
    return value if value in ERGO_PROXY_VALUES else 'none'


def ergo_broker(values: Mapping[str, str]) -> str:
    value = _get(values, 'ERGO_BROKER', 'local').lower()
    return value if value in ERGO_BROKER_VALUES else 'local'


def ergo_db(values: Mapping[str, str]) -> str:
    value = _get(values, 'ERGO_DB', 'portable_postgres').lower()
    return value if value in ERGO_DB_VALUES else 'portable_postgres'


def ergo_jupyter(values: Mapping[str, str]) -> str:
    value = _get(values, 'ERGO_JUPYTER', 'none').lower()
    return value if value in ERGO_JUPYTER_VALUES else 'none'


def ergo_email(values: Mapping[str, str]) -> str:
    value = _get(values, 'ERGO_EMAIL', 'none').lower()
    return value if value in ERGO_EMAIL_VALUES else 'none'


def ergo_media(values: Mapping[str, str]) -> str:
    value = _get(values, 'ERGO_MEDIA', 'local').lower()
    return value if value in ERGO_MEDIA_VALUES else 'local'


def ergo_realtime(values: Mapping[str, str]) -> str:
    value = _get(values, 'ERGO_REALTIME', 'websocket').lower()
    return value if value in ERGO_REALTIME_VALUES else 'websocket'


def normalize_deploy_type(raw: str, default: str = 'development') -> str:
    value = (raw or '').strip().lower()
    value = _ERGO_ENV_ALIASES.get(value, value)
    return value if value in ERGO_ENV_VALUES else default


def ergo_env(values: Mapping[str, str]) -> str:
    """development | production (допускаются alias: dev | prod)."""
    return normalize_deploy_type(_get(values, 'ERGO_ENV', 'development'))


def ergo_security(values: Mapping[str, str]) -> str:
    """Уровень безопасности: open | standard | hardened | maximum (default standard)."""
    value = _get(values, 'ERGO_SECURITY', 'standard').lower()
    return value if value in ERGO_SECURITY_VALUES else 'standard'


def ergo_security_enforce(values: Mapping[str, str]) -> str:
    """Реакция CLI на нарушения: off | warn | raise (default warn)."""
    value = _get(values, 'ERGO_SECURITY_ENFORCE', 'warn').lower()
    return value if value in ERGO_SECURITY_ENFORCE_VALUES else 'warn'


def security_level_rank(level: str) -> int:
    """Ранг уровня; неизвестный уровень → rank of standard."""
    return _ERGO_SECURITY_RANKS.get((level or '').strip().lower(), 1)


def ergo_security_is_explicit(values: Mapping[str, str]) -> bool:
    return _has_explicit(values, 'ERGO_SECURITY')


def effective_deploy_type(
    values: Mapping[str, str],
    *,
    override_key: str | None = None,
) -> str:
    """
    Режим развёртывания: ERGO_ENV, либо явный override
    (API_DEPLOY_TYPE / CLIENT_DEPLOY_TYPE / MEDIA_API_DEPLOY_TYPE).
    """
    if override_key and _has_explicit(values, override_key):
        return normalize_deploy_type(_get(values, override_key))
    return ergo_env(values)


def effective_docker_enabled(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'DOCKER_ENABLED'):
        return env_bool(_get(values, 'DOCKER_ENABLED'))
    return ergo_runtime(values) == 'docker'


def effective_nginx_enabled(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'NGINX_ENABLED'):
        return env_bool(_get(values, 'NGINX_ENABLED'))
    return ergo_proxy(values) == 'nginx'


def effective_redis_enabled(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'REDIS_ENABLED'):
        return env_bool(_get(values, 'REDIS_ENABLED'))
    return ergo_broker(values) == 'redis'


def effective_postgres_force_install(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'POSTGRES_FORCE_INSTALL'):
        return env_bool(_get(values, 'POSTGRES_FORCE_INSTALL'))
    return ergo_db(values) == 'portable_postgres'


def should_install_portable_postgres(values: Mapping[str, str]) -> bool:
    """setup-full / install-postgres: только portable_postgres (или force)."""
    db = ergo_db(values)
    if db == 'portable_postgres':
        return True
    if _has_explicit(values, 'POSTGRES_FORCE_INSTALL') and env_bool(
        _get(values, 'POSTGRES_FORCE_INSTALL')
    ):
        return True
    return False


def default_engine_for_ergo_db(values: Mapping[str, str]) -> str | None:
    """
    Engine для секции default из ERGO_DB.

    None — ERGO_DB не задан осмысленно (не должно случаться); вызывающий
    может оставить engine из yaml.
    """
    if not _has_explicit(values, 'ERGO_DB') and not _get(values, 'ERGO_DB'):
        return None
    return _ERGO_DB_TO_ENGINE.get(ergo_db(values))


def apply_ergo_db_engine(db_config: dict, values: Mapping[str, str]) -> dict:
    """Копия конфига секции default с engine из ERGO_DB, если режим задан."""
    engine = default_engine_for_ergo_db(values)
    if not engine:
        return db_config
    updated = dict(db_config)
    updated['engine'] = engine
    return updated


def effective_docker_profile_postgres(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'DOCKER_PROFILE_POSTGRES'):
        return env_bool(_get(values, 'DOCKER_PROFILE_POSTGRES'))
    db = ergo_db(values)
    return db in ('postgres', 'portable_postgres')


def effective_jupyter_enabled(values: Mapping[str, str]) -> bool:
    """Jupyter включён, если ERGO_JUPYTER ≠ none."""
    return ergo_jupyter(values) != 'none'


def effective_jupyter_access_mode(values: Mapping[str, str]) -> str | None:
    """
    Режим доступа Jupyter из ERGO_JUPYTER.

    None — режим none или auto (нужна эвристика behind_nginx / allow_remote).
    Явный API_JUPYTER_ACCESS_MODE (local|lan|nginx) имеет приоритет у вызывающего.
    """
    mode = ergo_jupyter(values)
    if mode in ('local', 'lan', 'nginx'):
        return mode
    return None


def effective_jupyter_behind_nginx(values: Mapping[str, str]) -> bool:
    """
    Jupyter слушает за nginx.

    True, если прокси включён и режим доступа nginx:
    ERGO_JUPYTER=nginx, явный API_JUPYTER_ACCESS_MODE=nginx,
    либо при auto — legacy API_JUPYTER_BEHIND_NGINX=true.
    """
    if not effective_nginx_enabled(values):
        return False
    explicit = _get(values, 'API_JUPYTER_ACCESS_MODE').lower()
    if explicit == 'nginx':
        return True
    if explicit in ('local', 'lan'):
        return False
    if ergo_jupyter(values) == 'nginx':
        return True
    return env_bool(_get(values, 'API_JUPYTER_BEHIND_NGINX'))


def effective_email_enabled(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'EMAIL_ENABLED'):
        return env_bool(_get(values, 'EMAIL_ENABLED'))
    return ergo_email(values) == 'smtp'


def effective_media_access_mode(values: Mapping[str, str]) -> str:
    """
    Режим доступа core/api к файлам: local | remote.

    Явный MEDIA_ACCESS_MODE имеет приоритет над ERGO_MEDIA.
    """
    if _has_explicit(values, 'MEDIA_ACCESS_MODE'):
        mode = _get(values, 'MEDIA_ACCESS_MODE').lower()
        if mode in ERGO_MEDIA_VALUES:
            return mode
    return ergo_media(values)


def effective_realtime_transport(values: Mapping[str, str]) -> str:
    """
    Транспорт realtime: websocket | sse | http_polling.

    Явный REALTIME_TRANSPORT имеет приоритет над ERGO_REALTIME.
    """
    if _has_explicit(values, 'REALTIME_TRANSPORT'):
        mode = _get(values, 'REALTIME_TRANSPORT').lower()
        if mode in ERGO_REALTIME_VALUES:
            return mode
    return ergo_realtime(values)


def effective_docker_profile_jupyter(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'DOCKER_PROFILE_JUPYTER'):
        return env_bool(_get(values, 'DOCKER_PROFILE_JUPYTER'))
    return effective_jupyter_enabled(values)


def effective_docker_profile_loadtest(values: Mapping[str, str]) -> bool:
    """Явный DOCKER_PROFILE_LOADTEST (по умолчанию выкл.)."""
    if _has_explicit(values, 'DOCKER_PROFILE_LOADTEST'):
        return env_bool(_get(values, 'DOCKER_PROFILE_LOADTEST'))
    return False


def effective_search_enabled(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'ERGO_SEARCH_ENABLED'):
        return env_bool(_get(values, 'ERGO_SEARCH_ENABLED'))
    return True


def effective_docker_profile_meilisearch(values: Mapping[str, str]) -> bool:
    if _has_explicit(values, 'DOCKER_PROFILE_MEILISEARCH'):
        return env_bool(_get(values, 'DOCKER_PROFILE_MEILISEARCH'))
    return effective_search_enabled(values)
