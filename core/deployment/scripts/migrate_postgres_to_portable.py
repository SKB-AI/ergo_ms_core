"""
Перенос данных PostgreSQL из системного инстанса в portable
(virtual_env/packages/postgres) через pg_dump / pg_restore.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
SCRIPTS_DIR = DEPLOYMENT_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from postgres_common import (  # noqa: E402
    DEFAULT_PORT,
    PORTABLE_DEFAULT_PORT,
    _parse_simple_yaml_section,
    effective_portable_port,
    is_installed,
    iter_database_section_names,
    load_db_defaults,
    ping_postgres,
    postgres_bin,
)
from project_layout import cache_tmp_dir, ensure_dir  # noqa: E402

DUMP_TIMEOUT_SEC = 3600
SOURCE_DEFAULT_PORT = DEFAULT_PORT


def _log(level: str, message: str) -> None:
    """Печать с немедленным flush — иначе в PowerShell прогресс часто не виден."""
    stream = sys.stderr if level == 'error' else sys.stdout
    print(format_console(level, message), file=stream, flush=True)


def _normalize_host(host: str) -> str:
    value = (host or '').strip().lower()
    if value in {'', 'localhost', '::1', '0.0.0.0'}:
        return '127.0.0.1'
    return value


def _pg_env(user: str, password: str, host: str) -> dict[str, str]:
    import os

    return {
        **os.environ,
        'PGUSER': user,
        'PGPASSWORD': password,
        'PGHOST': _normalize_host(host),
        # Иначе psql на Windows часто пишет ошибки в CP1251, а Python ждёт UTF-8.
        'PGCLIENTENCODING': 'UTF8',
    }


def load_postgresql_sections(root: Path) -> list[dict[str, str]]:
    """Секции databases.yaml с engine=postgresql (default — postgresql, если engine не задан)."""
    path = root / 'databases.yaml'
    defaults = load_db_defaults(root)
    if not path.is_file():
        return [{
            'section': 'default',
            'name': defaults['name'],
            'user': defaults['user'],
            'password': defaults['password'],
            'host': defaults.get('host') or 'localhost',
            'port': defaults.get('port') or str(PORTABLE_DEFAULT_PORT),
        }]

    text = path.read_text(encoding='utf-8')
    sections: list[dict[str, str]] = []
    for section_name in iter_database_section_names(text):
        section = _parse_simple_yaml_section(text, section_name)
        engine = (section.get('engine') or '').strip().lower()
        if section_name == 'default' and not engine:
            engine = 'postgresql'
        if engine not in {'postgresql', 'postgres', 'django.db.backends.postgresql'}:
            continue
        if not section.get('name'):
            continue
        sections.append({
            'section': section_name,
            'name': section.get('name') or defaults['name'],
            'user': section.get('user') or defaults['user'],
            'password': section.get('password') or defaults['password'],
            'host': section.get('host') or defaults.get('host') or 'localhost',
            'port': section.get('port') or defaults.get('port') or str(PORTABLE_DEFAULT_PORT),
        })
    if not sections:
        raise RuntimeError(t('postgres_migrate_no_sections'))
    return sections


def _decode_pg_output(data: bytes | None) -> str:
    if not data:
        return ''
    for encoding in ('utf-8', 'cp1251', 'cp866'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def _run_tool(
    root: Path,
    tool: str,
    args: list[str],
    *,
    env: dict[str, str],
    timeout: float | None = DUMP_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    binary = postgres_bin(root, tool)
    if not binary.is_file():
        raise RuntimeError(t('postgres_tool_not_found', tool=tool, binary=binary))
    # bytes + ручной decode: на Windows psql часто пишет stderr в CP1251.
    raw = subprocess.run(
        [str(binary), *args],
        capture_output=True,
        text=False,
        check=False,
        env=env,
        timeout=timeout,
    )
    return subprocess.CompletedProcess(
        args=raw.args,
        returncode=raw.returncode,
        stdout=_decode_pg_output(raw.stdout),
        stderr=_decode_pg_output(raw.stderr),
    )


def _tool_failed(result: subprocess.CompletedProcess[str], label: str) -> None:
    detail = (result.stderr or result.stdout or '').strip()
    if detail:
        print(detail, file=sys.stderr)
    raise RuntimeError(t('postgres_tool_failed', label=label, code=result.returncode))


def _psql_scalar(
    root: Path,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    sql: str,
) -> str:
    result = _run_tool(
        root,
        'psql',
        ['-h', _normalize_host(host), '-p', str(port), '-d', database, '-tAc', sql],
        env=_pg_env(user, password, host),
        timeout=60,
    )
    if result.returncode != 0:
        _tool_failed(result, 'psql')
    return (result.stdout or '').strip()


def _server_major(
    root: Path,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
) -> int:
    value = _psql_scalar(
        root,
        host=host,
        port=port,
        user=user,
        password=password,
        database='postgres',
        sql='SHOW server_version_num',
    )
    try:
        return int(value) // 10000
    except ValueError as exc:
        raise RuntimeError(t('postgres_source_version_failed', value=repr(value))) from exc


def _client_major(root: Path) -> int:
    result = _run_tool(root, 'pg_dump', ['--version'], env=_pg_env('postgres', '', '127.0.0.1'), timeout=30)
    text = ((result.stdout or '') + (result.stderr or '')).strip()
    match = re.search(r'(\d+)\.\d+', text)
    if not match:
        raise RuntimeError(t('postgres_pg_dump_version_failed', value=repr(text)))
    return int(match.group(1))


def _database_has_user_tables(
    root: Path,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> bool:
    value = _psql_scalar(
        root,
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        sql=(
            "SELECT COUNT(*) FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg_toast%' "
            "AND n.nspname NOT LIKE 'pg_temp_%' "
            "AND c.relkind IN ('r', 'p', 'v', 'm')"
        ),
    )
    try:
        return int(value) > 0
    except ValueError:
        return bool(value and value != '0')


def _database_exists(
    root: Path,
    *,
    host: str,
    port: int,
    admin_user: str,
    admin_password: str,
    database: str,
) -> bool:
    value = _psql_scalar(
        root,
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database='postgres',
        sql=f"SELECT 1 FROM pg_database WHERE datname = '{database.replace(chr(39), chr(39)+chr(39))}'",
    )
    return value == '1'


def _ensure_role_and_database(
    root: Path,
    *,
    host: str,
    port: int,
    admin_user: str,
    admin_password: str,
    db_name: str,
    db_user: str,
    db_password: str,
) -> None:
    env = _pg_env(admin_user, admin_password, host)

    def exec_sql(sql: str, database: str = 'postgres') -> None:
        result = _run_tool(
            root,
            'psql',
            ['-v', 'ON_ERROR_STOP=1', '-h', _normalize_host(host), '-p', str(port), '-d', database, '-c', sql],
            env=env,
            timeout=120,
        )
        if result.returncode != 0:
            _tool_failed(result, 'psql')

    role_exists = _psql_scalar(
        root,
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database='postgres',
        sql=f"SELECT 1 FROM pg_roles WHERE rolname = '{db_user.replace(chr(39), chr(39)+chr(39))}'",
    ) == '1'
    if not role_exists:
        safe_pass = db_password.replace("'", "''")
        exec_sql(f"CREATE ROLE \"{db_user}\" LOGIN PASSWORD '{safe_pass}'")
        print(format_console('ok', t('postgres_role_created', user=db_user)))

    if not _database_exists(
        root,
        host=host,
        port=port,
        admin_user=admin_user,
        admin_password=admin_password,
        database=db_name,
    ):
        exec_sql(f'CREATE DATABASE "{db_name}" OWNER "{db_user}"')
        print(format_console('ok', t('postgres_db_created', dbname=db_name)))


def _recreate_database(
    root: Path,
    *,
    host: str,
    port: int,
    admin_user: str,
    admin_password: str,
    db_name: str,
    db_user: str,
) -> None:
    env = _pg_env(admin_user, admin_password, host)
    drop_sql = f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'
    result = _run_tool(
        root,
        'psql',
        ['-v', 'ON_ERROR_STOP=1', '-h', _normalize_host(host), '-p', str(port), '-d', 'postgres', '-c', drop_sql],
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        # PG < 13 без WITH (FORCE)
        drop_sql = f'DROP DATABASE IF EXISTS "{db_name}"'
        result = _run_tool(
            root,
            'psql',
            ['-v', 'ON_ERROR_STOP=1', '-h', _normalize_host(host), '-p', str(port), '-d', 'postgres', '-c', drop_sql],
            env=env,
            timeout=120,
        )
        if result.returncode != 0:
            _tool_failed(result, 'DROP DATABASE')
    create = _run_tool(
        root,
        'psql',
        [
            '-v', 'ON_ERROR_STOP=1',
            '-h', _normalize_host(host), '-p', str(port), '-d', 'postgres',
            '-c', f'CREATE DATABASE "{db_name}" OWNER "{db_user}"',
        ],
        env=env,
        timeout=120,
    )
    if create.returncode != 0:
        _tool_failed(create, 'CREATE DATABASE')
    print(format_console('ok', t('postgres_db_recreated', dbname=db_name)))


def _portable_accepts_connections(root: Path, port: int, timeout_sec: float = 2.0) -> bool:
    """Проверка без пароля: yaml может указывать пароль системного Postgres, не portable."""
    binary = postgres_bin(root, 'pg_isready')
    if not binary.is_file():
        return ping_postgres(root, port=port, timeout_sec=timeout_sec)
    try:
        result = subprocess.run(
            [str(binary), '-h', '127.0.0.1', '-p', str(port)],
            capture_output=True,
            text=False,
            timeout=timeout_sec,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _wait_portable(root: Path, port: int, timeout_sec: float = 30.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _portable_accepts_connections(root, port, timeout_sec=2.0):
            return
        time.sleep(1)
    raise RuntimeError(t('postgres_portable_not_accepting', port=port))


def _assert_target_admin(
    root: Path,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
) -> None:
    try:
        _psql_scalar(
            root,
            host=host,
            port=port,
            user=user,
            password=password,
            database='postgres',
            sql='SELECT 1',
        )
    except RuntimeError as exc:
        raise RuntimeError(
            t(
                'postgres_target_login_failed',
                user=user,
                host=host,
                port=port,
                exc=exc,
            )
        ) from exc


def update_yaml_ports_to_portable(root: Path, port: int, host: str = '127.0.0.1') -> list[str]:
    """Выставляет host/port portable во всех PostgreSQL-секциях databases.yaml."""
    path = root / 'databases.yaml'
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8')
    pg_sections = {s['section'] for s in load_postgresql_sections(root)}
    if not pg_sections:
        return []

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_databases = False
    current_section: str | None = None
    section_indent = -1
    updated: list[str] = []

    for raw in lines:
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(' ')) if stripped else 0
        if stripped == 'databases:':
            in_databases = True
            current_section = None
            out.append(raw)
            continue
        if in_databases and indent == 2 and stripped.endswith(':'):
            current_section = stripped[:-1].strip()
            section_indent = indent
            out.append(raw)
            continue
        if (
            in_databases
            and current_section in pg_sections
            and indent > section_indent
            and ':' in stripped
            and not stripped.startswith('#')
        ):
            key = stripped.partition(':')[0].strip()
            prefix = raw[: len(raw) - len(raw.lstrip(' '))]
            newline = '\n' if raw.endswith('\n') else ''
            if key == 'host':
                out.append(f'{prefix}host: "{host}"{newline}')
                if current_section not in updated:
                    updated.append(current_section)
                continue
            if key == 'port':
                out.append(f'{prefix}port: {port}{newline}')
                if current_section not in updated:
                    updated.append(current_section)
                continue
        out.append(raw)

    path.write_text(''.join(out), encoding='utf-8')
    return updated


def migrate(
    root: Path,
    *,
    source_host: str,
    source_port: int,
    source_user: str,
    source_password: str,
    force: bool,
    dry_run: bool,
) -> int:
    if not is_installed(root):
        print(
            format_console('error', t('postgres_migrate_not_installed')),
            file=sys.stderr,
        )
        return 1

    target_port = effective_portable_port(root)
    admin = load_db_defaults(root)
    admin_user = admin['user']
    admin_password = admin['password']
    target_host = '127.0.0.1'
    src_host = _normalize_host(source_host)

    try:
        sections = load_postgresql_sections(root)
        _wait_portable(root, target_port)
        _assert_target_admin(
            root,
            host=target_host,
            port=target_port,
            user=admin_user,
            password=admin_password,
        )
        client_major = _client_major(root)
        # Проверка источника один раз (суперпользователь системного Postgres).
        source_major = _server_major(
            root,
            host=src_host,
            port=source_port,
            user=source_user,
            password=source_password,
        )
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        _log('error', str(exc))
        return 1

    if src_host == _normalize_host(target_host) and int(source_port) == int(target_port):
        _log(
            'error',
            t(
                'postgres_migrate_same_source_target',
                host=src_host,
                port=source_port,
                default_port=SOURCE_DEFAULT_PORT,
            ),
        )
        return 1

    if client_major < source_major:
        _log(
            'error',
            t(
                'postgres_migrate_client_older',
                client_major=client_major,
                source_major=source_major,
            ),
        )
        return 1

    total = len(sections)
    _log(
        'info',
        t('postgres_migrate_target_info', host=target_host, port=target_port),
    )
    _log(
        'info',
        t(
            'postgres_migrate_source_info',
            user=source_user,
            host=src_host,
            port=source_port,
        ),
    )
    _log('info', t('postgres_migrate_db_count', total=total))

    tmp_root = ensure_dir(cache_tmp_dir(root) / 'postgres_migrate')
    dump_paths: list[Path] = []

    try:
        for index, section in enumerate(sections, start=1):
            section_name = section['section']
            db_name = section['name']
            db_user = section['user']
            db_password = section['password']
            src_port = int(source_port)
            step = f'{index}/{total}'

            _log(
                'info',
                t(
                    'postgres_migrate_db_step',
                    step=step,
                    section=section_name,
                    db_name=db_name,
                    src_host=src_host,
                    src_port=src_port,
                    target_host=target_host,
                    target_port=target_port,
                ),
            )

            target_exists = _database_exists(
                root,
                host=target_host,
                port=target_port,
                admin_user=admin_user,
                admin_password=admin_password,
                database=db_name,
            )
            target_busy = False
            if target_exists:
                target_busy = _database_has_user_tables(
                    root,
                    host=target_host,
                    port=target_port,
                    user=admin_user,
                    password=admin_password,
                    database=db_name,
                )
            if target_busy and not force:
                _log(
                    'error',
                    t(
                        'postgres_migrate_target_not_empty',
                        step=step,
                        section=section_name,
                        db_name=db_name,
                    ),
                )
                return 1

            if dry_run:
                action = (
                    t('postgres_migrate_action_overwrite')
                    if target_busy
                    else t('postgres_migrate_action_load')
                )
                _log(
                    'ok',
                    t(
                        'postgres_migrate_dry_run_action',
                        step=step,
                        section=section_name,
                        action=action,
                        db_name=db_name,
                    ),
                )
                continue

            dump_path = tmp_root / f'{section_name}_{db_name}.dump'
            dump_paths.append(dump_path)
            _log('info', t('postgres_migrate_pg_dump', step=step, db_name=db_name))
            dump = _run_tool(
                root,
                'pg_dump',
                [
                    '-h', src_host,
                    '-p', str(src_port),
                    '-d', db_name,
                    '-Fc',
                    '--no-owner',
                    '--no-acl',
                    '-f', str(dump_path),
                ],
                env=_pg_env(source_user, source_password, src_host),
            )
            if dump.returncode != 0:
                _tool_failed(dump, f'pg_dump [{section_name}]')
            if dump_path.is_file():
                size_mb = dump_path.stat().st_size / (1024 * 1024)
                _log(
                    'info',
                    t('postgres_migrate_dump_ready', step=step, size_mb=f'{size_mb:.1f}'),
                )

            _ensure_role_and_database(
                root,
                host=target_host,
                port=target_port,
                admin_user=admin_user,
                admin_password=admin_password,
                db_name=db_name,
                db_user=db_user,
                db_password=db_password,
            )
            if target_exists:
                # install-postgres заранее создаёт schema core / pg_trgm — чистый restore иначе падает.
                _recreate_database(
                    root,
                    host=target_host,
                    port=target_port,
                    admin_user=admin_user,
                    admin_password=admin_password,
                    db_name=db_name,
                    db_user=db_user,
                )

            _log('info', t('postgres_migrate_pg_restore', step=step, db_name=db_name))
            restore = _run_tool(
                root,
                'pg_restore',
                [
                    '-h', target_host,
                    '-p', str(target_port),
                    '-d', db_name,
                    '--no-owner',
                    '--no-acl',
                    str(dump_path),
                ],
                env=_pg_env(admin_user, admin_password, target_host),
            )
            if restore.returncode != 0:
                loaded = _database_has_user_tables(
                    root,
                    host=target_host,
                    port=target_port,
                    user=admin_user,
                    password=admin_password,
                    database=db_name,
                )
                if not loaded:
                    _tool_failed(restore, f'pg_restore [{section_name}]')
                detail = (restore.stderr or restore.stdout or '').strip()
                if detail:
                    print(detail, file=sys.stderr)
                _log('warning', t('postgres_tool_failed', label=f'pg_restore [{section_name}]', code=restore.returncode))
            _log(
                'ok',
                t(
                    'postgres_migrate_done_db',
                    step=step,
                    section=section_name,
                    db_name=db_name,
                ),
            )

        if dry_run:
            _log('ok', t('postgres_migrate_dry_run_done'))
            return 0

        updated = update_yaml_ports_to_portable(root, target_port, host=target_host)
        _log('ok', t('postgres_migrate_complete'))
        if updated:
            _log(
                'ok',
                t(
                    'postgres_migrate_yaml_updated',
                    port=target_port,
                    sections=', '.join(updated),
                ),
            )
        _log('info', t('postgres_migrate_restart_api'))
        return 0
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        _log('error', str(exc))
        return 1
    finally:
        for path in dump_paths:
            path.unlink(missing_ok=True)
        if tmp_root.is_dir() and not any(tmp_root.iterdir()):
            shutil.rmtree(tmp_root, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=t('postgres_migrate_description'),
    )
    parser.add_argument(
        '--root',
        type=Path,
        default=PROJECT_ROOT,
        help=t('help_project_root'),
    )
    parser.add_argument(
        '--source-port',
        type=int,
        default=SOURCE_DEFAULT_PORT,
        help=t('postgres_migrate_help_source_port', default=SOURCE_DEFAULT_PORT),
    )
    parser.add_argument(
        '--source-host',
        default='127.0.0.1',
        help=t('postgres_migrate_help_source_host'),
    )
    parser.add_argument(
        '--source-user',
        default='postgres',
        help=t('postgres_migrate_help_source_user'),
    )
    parser.add_argument(
        '--source-password',
        required=True,
        help=t('postgres_migrate_help_source_password'),
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help=t('postgres_migrate_help_force'),
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help=t('postgres_migrate_help_dry_run'),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    return migrate(
        root,
        source_host=args.source_host,
        source_port=args.source_port,
        source_user=args.source_user,
        source_password=args.source_password,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == '__main__':
    raise SystemExit(main())
