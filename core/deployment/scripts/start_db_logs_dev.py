"""
Терминал логов default БД для VS Code Start All Services / ergoms start-db-dev.

Поведение зависит от ERGO_DB:
- portable_postgres — tail логов portable в virtual_env/packages/postgres/logs;
- postgres / mysql / mssql — tail системных журналов (если найдены) или подсказка;
- sqlite — нет серверных логов; хвост api.log (ошибки ORM) + путь к файлу БД;
- ERGO_RUNTIME=docker — docker-logs -f для сервиса БД Compose.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = SCRIPTS_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from deployment_env import (  # noqa: E402
    PROJECT_ROOT,
    get_ergo_db,
    is_docker_enabled,
    read_env,
)
from log_env import log_file_path  # noqa: E402
from nginx_foreground import _configure_stdio_utf8, tail_log_files  # noqa: E402

_DB_LABELS = {
    'sqlite': 'SQLite',
    'postgres': 'PostgreSQL',
    'portable_postgres': 'PostgreSQL (portable)',
    'mysql': 'MySQL',
    'mssql': 'Microsoft SQL Server',
}


def current_ergo_db() -> str:
    return get_ergo_db()


def db_service_label(db_mode: str | None = None) -> str:
    mode = db_mode or current_ergo_db()
    if should_use_portable_postgres_logs(mode):
        return _DB_LABELS['portable_postgres']
    return _DB_LABELS.get(mode, mode)


def db_terminal_key(db_mode: str | None = None) -> str:
    """Короткий slug СУБД (lowercase): postgres / sqlite / mysql / mssql."""
    mode = db_mode or current_ergo_db()
    if should_use_portable_postgres_logs(mode) or mode in ('postgres', 'portable_postgres'):
        return 'postgres'
    if mode in ('sqlite', 'mysql', 'mssql'):
        return mode
    return 'postgres'


def db_terminal_title(db_mode: str | None = None) -> str:
    """Имя вкладки терминала VS Code в стиле API / Redis: Postgres, MySQL, …"""
    slug = db_terminal_key(db_mode)
    titles = {
        'postgres': 'Postgres',
        'sqlite': 'SQLite',
        'mysql': 'MySQL',
        'mssql': 'MSSQL',
    }
    return titles.get(slug, slug.capitalize())


def _is_docker_runtime() -> bool:
    return read_env('ERGO_RUNTIME', 'host').strip().lower() == 'docker'


def portable_postgres_log_paths(root: Path | None = None) -> list[Path]:
    from postgres_common import postgres_packages_dir  # noqa: WPS433

    project_root = root or PROJECT_ROOT
    package_logs = postgres_packages_dir(project_root) / 'logs'
    paths = [log_file_path('POSTGRES', project_root)]
    for name in (
        'postgresql.log',
        'pg_ctl.log',
        'service_stdout.log',
        'service_stderr.log',
    ):
        path = package_logs / name
        if path not in paths:
            paths.append(path)
    return paths


def portable_postgres_installed(root: Path | None = None) -> bool:
    from postgres_common import postgres_packages_dir  # noqa: WPS433

    packages = postgres_packages_dir(root or PROJECT_ROOT)
    return (packages / 'VERSION').is_file() or (packages / 'data').is_dir()


def should_use_portable_postgres_logs(db_mode: str | None = None, root: Path | None = None) -> bool:
    """Portable-логи, если режим portable или default указывает на порт portable-кластера."""
    from postgres_common import (  # noqa: WPS433
        PORTABLE_DEFAULT_PORT,
        load_db_defaults,
        read_portable_listen_port,
    )

    project_root = root or PROJECT_ROOT
    mode = db_mode or current_ergo_db()
    if mode == 'portable_postgres':
        return True
    if mode != 'postgres' or not portable_postgres_installed(project_root):
        return False
    defaults = load_db_defaults(project_root)
    portable_port = read_portable_listen_port(project_root) or PORTABLE_DEFAULT_PORT
    try:
        cfg_port = int(str(defaults.get('port') or '0'))
    except ValueError:
        return False
    return cfg_port == int(portable_port)


def sqlite_db_path(root: Path | None = None) -> Path:
    project_root = root or PROJECT_ROOT
    return project_root / 'virtual_env' / 'resources' / 'db.sqlite3'


def _default_connection_summary(root: Path | None = None) -> str:
    from postgres_common import load_db_defaults  # noqa: WPS433

    project_root = root or PROJECT_ROOT
    defaults = load_db_defaults(project_root)
    host = defaults.get('host', '')
    port = defaults.get('port', '')
    name = defaults.get('name', '')
    user = defaults.get('user', '')
    return t(
        'db_logs_connection',
        host=host or '—',
        port=port or '—',
        name=name or '—',
        user=user or '—',
    )


def _existing_files(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.is_file()]


def _system_postgres_log_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.name == 'nt':
        program_files = [
            Path(os.environ.get('ProgramFiles', r'C:\Program Files')),
            Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')),
        ]
        for root in program_files:
            pg_root = root / 'PostgreSQL'
            if not pg_root.is_dir():
                continue
            for version_dir in pg_root.iterdir():
                log_dir = version_dir / 'data' / 'log'
                if log_dir.is_dir():
                    candidates.extend(sorted(log_dir.glob('*.log')))
    else:
        for directory in (
            Path('/var/log/postgresql'),
            Path('/var/lib/pgsql/data/log'),
            Path('/var/lib/pgsql/data/pg_log'),
        ):
            if directory.is_dir():
                candidates.extend(sorted(directory.glob('*.log')))
    return candidates


def _system_mysql_log_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.name == 'nt':
        program_data = Path(os.environ.get('ProgramData', r'C:\ProgramData'))
        mysql_root = program_data / 'MySQL'
        if mysql_root.is_dir():
            for path in mysql_root.rglob('*.err'):
                candidates.append(path)
            for path in mysql_root.rglob('*.log'):
                candidates.append(path)
    else:
        for path in (
            Path('/var/log/mysql/error.log'),
            Path('/var/log/mysqld.log'),
            Path('/var/log/mysql/mysql.log'),
        ):
            if path.is_file():
                candidates.append(path)
        error_dir = Path('/var/log/mysql')
        if error_dir.is_dir():
            candidates.extend(sorted(error_dir.glob('*.log')))
    # unique preserve order
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _system_mssql_log_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.name == 'nt':
        program_files = Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
        mssql = program_files / 'Microsoft SQL Server'
        if mssql.is_dir():
            for path in mssql.rglob('ERRORLOG'):
                candidates.append(path)
            for path in mssql.rglob('ERRORLOG.*'):
                candidates.append(path)
    else:
        for directory in (
            Path('/var/opt/mssql/log'),
            Path('/var/opt/mssql/data'),
        ):
            if directory.is_dir():
                candidates.extend(sorted(directory.glob('ERRORLOG*')))
    return candidates


def resolve_default_db_log_paths(
    db_mode: str | None = None,
    root: Path | None = None,
) -> list[Path]:
    """Пути журналов default БД (могут ещё не существовать на диске)."""
    project_root = root or PROJECT_ROOT
    mode = db_mode or current_ergo_db()
    if should_use_portable_postgres_logs(mode, project_root):
        return portable_postgres_log_paths(project_root)
    if mode == 'sqlite':
        return [log_file_path('API', project_root)]
    if mode == 'postgres':
        return _system_postgres_log_candidates()
    if mode == 'mysql':
        return _system_mysql_log_candidates()
    if mode == 'mssql':
        return _system_mssql_log_candidates()
    return []


def _docker_db_service(db_mode: str) -> str | None:
    if db_mode in ('postgres', 'portable_postgres'):
        return read_env('DOCKER_SERVICE_POSTGRES', 'postgres').strip() or 'postgres'
    if db_mode == 'mysql':
        return read_env('DOCKER_SERVICE_MYSQL', 'mysql').strip() or 'mysql'
    if db_mode == 'mssql':
        return read_env('DOCKER_SERVICE_MSSQL', 'mssql').strip() or 'mssql'
    return None


def _follow_docker_logs(service: str) -> int:
    docker_cli = _DEPLOYMENT_DIR / 'docker' / 'docker_cli.py'
    print(format_console('info', t('db_logs_docker_follow', service=service)))
    return subprocess.call(
        [sys.executable, str(docker_cli), 'logs', '-f', service],
        cwd=str(PROJECT_ROOT),
    )


def _wait_idle(service: str) -> int:
    print(format_console('info', t('log_files_not_found_waiting', service=service)))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print(format_console('info', t('log_stream_stopped')))
        return 0


def _print_header(db_mode: str) -> None:
    label = db_service_label(db_mode)
    print(format_console('info', t('db_logs_header', mode=db_mode, label=label)))
    if db_mode == 'sqlite':
        print(format_console('info', t('db_logs_sqlite_path', path=str(sqlite_db_path()))))
        print(format_console('info', t('db_logs_sqlite_api_hint')))
    else:
        print(format_console('info', _default_connection_summary()))


def main() -> int:
    _configure_stdio_utf8()
    db_mode = current_ergo_db()
    label = db_service_label(db_mode)
    _print_header(db_mode)

    if _is_docker_runtime() and is_docker_enabled():
        service = _docker_db_service(db_mode)
        if service:
            return _follow_docker_logs(service)
        print(format_console('warning', t('db_logs_docker_no_service', mode=db_mode)))
        return _wait_idle(label)

    if should_use_portable_postgres_logs(db_mode):
        if not portable_postgres_installed(PROJECT_ROOT):
            print(format_console('error', t('db_logs_portable_not_installed')))
            return 1
        return tail_log_files(
            portable_postgres_log_paths(PROJECT_ROOT),
            service=label,
            process_keeps_running=True,
            initial_lines=500,
        )

    if db_mode == 'sqlite':
        return tail_log_files(
            [log_file_path('API', PROJECT_ROOT)],
            service=label,
            process_keeps_running=True,
        )

    paths = resolve_default_db_log_paths(db_mode)
    existing = _existing_files(paths)
    if existing:
        return tail_log_files(existing, service=label, process_keeps_running=True)

    hint_key = {
        'postgres': 'db_logs_system_postgres_hint',
        'mysql': 'db_logs_system_mysql_hint',
        'mssql': 'db_logs_system_mssql_hint',
    }.get(db_mode)
    if hint_key:
        print(format_console('warning', t(hint_key)))
    return _wait_idle(label)


if __name__ == '__main__':
    raise SystemExit(main())
