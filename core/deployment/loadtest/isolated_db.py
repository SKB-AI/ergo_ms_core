"""
Клон Postgres для loadtest (--isolated-db).

CREATE DATABASE … WITH TEMPLATE; артефакты в virtual_env/cache/loadtest/.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from project_layout import cache_loadtest_dir, ensure_dir  # noqa: E402

_PG_ENGINES = frozenset({'postgresql', 'postgres', 'django.db.backends.postgresql'})
LOADTEST_DB_SUFFIX = '_loadtest'
DEFAULT_LOADTEST_API_PORT = 18000


@dataclass(frozen=True)
class DefaultDbConfig:
    engine: str
    name: str
    user: str
    password: str
    host: str
    port: int


def databases_yaml_path(root: Path) -> Path:
    return root / 'databases.yaml'


def load_default_db(root: Path) -> DefaultDbConfig:
    path = databases_yaml_path(root)
    if not path.is_file():
        raise RuntimeError(f'databases.yaml not found: {path}')
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f'invalid databases.yaml: {exc}') from exc
    if not isinstance(raw, dict):
        raise RuntimeError('databases.yaml root must be object')
    databases = raw.get('databases')
    if not isinstance(databases, dict):
        raise RuntimeError('databases.yaml: missing databases')
    section = databases.get('default')
    if not isinstance(section, dict):
        raise RuntimeError('databases.yaml: missing databases.default')
    engine = str(section.get('engine') or '').strip().lower()
    name = str(section.get('name') or '').strip()
    user = str(section.get('user') or '').strip()
    password = str(section.get('password') or '')
    host = str(section.get('host') or '127.0.0.1').strip() or '127.0.0.1'
    try:
        port = int(section.get('port') or 5432)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'invalid databases.default.port: {exc}') from exc
    if not name:
        raise RuntimeError('databases.default.name is empty')
    if not user:
        raise RuntimeError('databases.default.user is empty')
    return DefaultDbConfig(
        engine=engine,
        name=name,
        user=user,
        password=password,
        host=host,
        port=port,
    )


def assert_postgres(cfg: DefaultDbConfig) -> None:
    if cfg.engine not in _PG_ENGINES and 'postgresql' not in cfg.engine:
        raise RuntimeError(
            f'--isolated-db requires PostgreSQL default DB, got engine={cfg.engine!r}'
        )


def clone_db_name(source_name: str) -> str:
    if source_name.endswith(LOADTEST_DB_SUFFIX):
        return source_name
    return f'{source_name}{LOADTEST_DB_SUFFIX}'


def _connect_admin(cfg: DefaultDbConfig):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            'psycopg is required for --isolated-db (install via ergoms poetry install)'
        ) from exc
    return psycopg.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        dbname='postgres',
        autocommit=True,
    )


def _terminate_db_backends(conn, db_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid <> pg_backend_pid()
            """,
            (db_name,),
        )


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def refresh_clone(cfg: DefaultDbConfig, *, clone_name: str | None = None) -> str:
    """
    DROP + CREATE DATABASE clone WITH TEMPLATE source.
    Возвращает имя клона.
    """
    assert_postgres(cfg)
    target = clone_name or clone_db_name(cfg.name)
    if target == cfg.name:
        raise RuntimeError('clone name must differ from source database name')

    with _connect_admin(cfg) as conn:
        _terminate_db_backends(conn, target)
        _terminate_db_backends(conn, cfg.name)
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS {_quote_ident(target)}')
            cur.execute(
                f'CREATE DATABASE {_quote_ident(target)} '
                f'WITH TEMPLATE {_quote_ident(cfg.name)} '
                f'OWNER {_quote_ident(cfg.user)}'
            )
    return target


def drop_clone(cfg: DefaultDbConfig, *, clone_name: str | None = None) -> None:
    assert_postgres(cfg)
    target = clone_name or clone_db_name(cfg.name)
    if target == cfg.name:
        raise RuntimeError('refusing to drop source database')
    with _connect_admin(cfg) as conn:
        _terminate_db_backends(conn, target)
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS {_quote_ident(target)}')


def write_loadtest_databases_yaml(
    root: Path,
    *,
    clone_name: str,
    host: str | None = None,
    port: int | None = None,
) -> Path:
    """
    Копия databases.yaml с default.name = clone_name.
    Путь: virtual_env/cache/loadtest/databases.yaml
    """
    src = databases_yaml_path(root)
    if not src.is_file():
        raise RuntimeError(f'databases.yaml not found: {src}')
    try:
        raw = yaml.safe_load(src.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f'invalid databases.yaml: {exc}') from exc
    if not isinstance(raw, dict):
        raise RuntimeError('databases.yaml root must be object')
    data = copy.deepcopy(raw)
    databases = data.get('databases')
    if not isinstance(databases, dict):
        raise RuntimeError('databases.yaml: missing databases')
    default = databases.get('default')
    if not isinstance(default, dict):
        raise RuntimeError('databases.yaml: missing databases.default')
    default['name'] = clone_name
    if host is not None:
        default['host'] = host
    if port is not None:
        default['port'] = port

    out_dir = ensure_dir(cache_loadtest_dir(root))
    out_path = out_dir / 'databases.yaml'
    out_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding='utf-8',
    )
    return out_path


def loadtest_env_for_yaml(yaml_path: Path, *, api_port: int) -> dict[str, str]:
    """Env для ephemeral API / provision против клона."""
    return {
        'ERGO_DATABASES_YAML': str(yaml_path.resolve()),
        'API_PORT': str(api_port),
    }
