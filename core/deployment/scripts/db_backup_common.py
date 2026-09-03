"""Снимок SQL-баз: секции, манифест, выбор pg_dump, Docker exec."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from db_backup_schedule import parse_backup_schedule  # noqa: E402, F401
from postgres_common import (  # noqa: E402
    _parse_simple_yaml_section,
    iter_database_section_names,
    load_db_defaults,
    postgres_bin,
)
from project_layout import backups_dir, ensure_dir  # noqa: E402

SNAPSHOT_STAMP_FORMAT = '%Y-%m-%d_%H%M%S'
MANIFEST_NAME = 'manifest.json'
DUMP_TIMEOUT_SEC = 3600
LOOPBACK_HOSTS = frozenset({'', 'localhost', '127.0.0.1', '::1', '0.0.0.0'})

_ENGINE_ALIASES = {
    'postgresql': 'postgresql',
    'postgres': 'postgresql',
    'django.db.backends.postgresql': 'postgresql',
    'sqlite': 'sqlite',
    'django.db.backends.sqlite3': 'sqlite',
    'mysql': 'mysql',
    'django.db.backends.mysql': 'mysql',
    'mssql': 'mssql',
    'sqlserver': 'mssql',
    'django.db.backends.mssql': 'mssql',
    'django.db.backends.sqlserver': 'mssql',
}

_ERGO_DB_ENGINE = {
    'sqlite': 'sqlite',
    'postgres': 'postgresql',
    'portable_postgres': 'postgresql',
    'mysql': 'mysql',
    'mssql': 'mssql',
}

_FILE_SUFFIX = {
    'postgresql': '.dump',
    'sqlite': '.sqlite3',
    'mysql': '.sql',
    'mssql': '.bak',
}


class BackupError(RuntimeError):
    """Ошибка снимка или восстановления без секретов в тексте."""


def snapshot_stamp(now: datetime | None = None) -> str:
    moment = now or datetime.now()
    return moment.strftime(SNAPSHOT_STAMP_FORMAT)


def new_snapshot_dir(root: Path, stamp: str | None = None) -> Path:
    dest = ensure_dir(backups_dir(root) / (stamp or snapshot_stamp()))
    return dest


def list_snapshot_dirs(root: Path) -> list[Path]:
    base = backups_dir(root)
    if not base.is_dir():
        return []
    found: list[Path] = []
    for child in base.iterdir():
        if child.is_dir() and (child / MANIFEST_NAME).is_file():
            found.append(child)
    return sorted(found, key=lambda path: path.name)


def latest_snapshot_dir(root: Path) -> Path | None:
    dirs = list_snapshot_dirs(root)
    return dirs[-1] if dirs else None


def parse_backup_keep(raw: str, default: int = 7) -> int:
    text = (raw or '').strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        return default
    return max(0, value)


def backup_keep_limit() -> int:
    from deployment_env import read_env  # noqa: WPS433

    return parse_backup_keep(read_env('POSTGRES_BACKUP_KEEP', '7'))


def prune_old_snapshots(root: Path, keep: int) -> list[Path]:
    """Оставляет не больше keep свежих снимков. keep=0 — ничего не удаляет."""
    if keep < 1:
        return []
    dirs = list_snapshot_dirs(root)
    extra = dirs[:-keep] if len(dirs) > keep else []
    removed: list[Path] = []
    for path in extra:
        shutil.rmtree(path)
        removed.append(path)
    return removed


def resolve_snapshot_dir(root: Path, raw: str) -> Path:
    value = (raw or '').strip()
    if not value:
        raise BackupError(t('db_backup_snapshot_missing'))
    candidates: list[Path] = []
    given = Path(value)
    if given.is_absolute():
        candidates.append(given)
    else:
        candidates.append((root / given).resolve())
        candidates.append((backups_dir(root) / given.name).resolve())
    seen: set[Path] = set()
    last = candidates[0]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        last = resolved
        if resolved.is_file() and resolved.name == MANIFEST_NAME:
            resolved = resolved.parent
        if resolved.is_dir() and (resolved / MANIFEST_NAME).is_file():
            return resolved
    raise BackupError(t('db_backup_snapshot_invalid', path=str(last)))


def normalize_host(host: str) -> str:
    value = (host or '').strip().lower()
    if value in LOOPBACK_HOSTS:
        return '127.0.0.1'
    return (host or '').strip() or '127.0.0.1'


def _is_loopback(host: str) -> bool:
    return (host or '').strip().lower() in LOOPBACK_HOSTS


def _normalize_engine(raw: str, *, section: str, ergo_db: str) -> str | None:
    engine = (raw or '').strip().lower()
    if not engine and section == 'default':
        return _ERGO_DB_ENGINE.get((ergo_db or '').strip().lower(), 'postgresql')
    if engine in {'redis', 'django.core.cache.backends.redis'}:
        return None
    mapped = _ENGINE_ALIASES.get(engine)
    return mapped


def load_sql_sections(
    root: Path,
    *,
    ergo_db: str = '',
    only_alias: str | None = None,
) -> list[dict[str, str]]:
    path = root / 'databases.yaml'
    defaults = load_db_defaults(root)
    if not path.is_file():
        inferred = _ERGO_DB_ENGINE.get((ergo_db or '').strip().lower(), 'postgresql')
        if only_alias and only_alias != 'default':
            raise BackupError(t('db_backup_alias_unknown', alias=only_alias))
        return [{
            'alias': 'default',
            'engine': inferred,
            'name': defaults['name'],
            'user': defaults['user'],
            'password': defaults['password'],
            'host': defaults.get('host') or 'localhost',
            'port': defaults.get('port') or '',
        }]

    text = path.read_text(encoding='utf-8')
    sections: list[dict[str, str]] = []
    for section_name in iter_database_section_names(text):
        raw = _parse_simple_yaml_section(text, section_name)
        engine = _normalize_engine(raw.get('engine') or '', section=section_name, ergo_db=ergo_db)
        if engine is None:
            continue
        if only_alias and section_name != only_alias:
            continue
        if engine != 'sqlite' and not raw.get('name') and section_name != 'default':
            continue
        sections.append({
            'alias': section_name,
            'engine': engine,
            'name': raw.get('name') or (defaults['name'] if section_name == 'default' else ''),
            'user': raw.get('user') or defaults.get('user') or '',
            'password': raw.get('password') or defaults.get('password') or '',
            'host': raw.get('host') or defaults.get('host') or 'localhost',
            'port': raw.get('port') or defaults.get('port') or '',
        })
    if only_alias and not sections:
        raise BackupError(t('db_backup_alias_unknown', alias=only_alias))
    if not sections:
        raise BackupError(t('db_backup_no_sections'))
    return sections


def dump_filename(alias: str, engine: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', alias).strip('_') or 'db'
    return f'{safe}{_FILE_SUFFIX.get(engine, ".dump")}'


def build_manifest(
    *,
    created_at: str,
    ergo_db: str,
    ergo_runtime: str,
    sections: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        'created_at': created_at,
        'ergo_db': ergo_db,
        'ergo_runtime': ergo_runtime,
        'sections': [
            {
                'alias': item['alias'],
                'engine': item['engine'],
                'name': item.get('name') or '',
                'filename': dump_filename(item['alias'], item['engine']),
            }
            for item in sections
        ],
    }


def write_manifest(snapshot: Path, payload: dict[str, Any]) -> Path:
    path = snapshot / MANIFEST_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def read_manifest(snapshot: Path) -> dict[str, Any]:
    path = snapshot / MANIFEST_NAME
    if not path.is_file():
        raise BackupError(t('db_backup_snapshot_invalid', path=str(snapshot)))
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise BackupError(t('db_backup_manifest_invalid', path=str(path))) from exc
    if not isinstance(data, dict) or not isinstance(data.get('sections'), list):
        raise BackupError(t('db_backup_manifest_invalid', path=str(path)))
    return data


def sqlite_db_path(root: Path, section: dict[str, str]) -> Path:
    name = (section.get('name') or '').strip()
    if name and (name.endswith('.sqlite3') or '/' in name or '\\' in name):
        path = Path(name)
        return path if path.is_absolute() else (root / path)
    return root / 'virtual_env' / 'resources' / 'db.sqlite3'


def copy_sqlite(source: Path, dest: Path) -> None:
    if not source.is_file():
        raise BackupError(t('db_backup_sqlite_missing', path=str(source)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src_conn = sqlite3.connect(str(source))
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
    except sqlite3.Error:
        shutil.copy2(source, dest)


def _pg_env(user: str, password: str, host: str) -> dict[str, str]:
    return {
        **os.environ,
        'PGUSER': user,
        'PGPASSWORD': password,
        'PGHOST': normalize_host(host),
        'PGCLIENTENCODING': 'UTF8',
    }


def _decode_pg_output(data: bytes | None) -> str:
    if not data:
        return ''
    for encoding in ('utf-8', 'cp1251', 'cp866'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def resolve_pg_tool(root: Path, tool: str) -> Path:
    portable = postgres_bin(root, tool)
    if portable.is_file():
        return portable
    found = shutil.which(tool)
    if found:
        return Path(found)
    raise BackupError(t('db_backup_pg_tool_missing', tool=tool))


def run_local_pg_tool(
    root: Path,
    tool: str,
    args: list[str],
    *,
    env: dict[str, str],
    timeout: float = DUMP_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    binary = resolve_pg_tool(root, tool)
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


def _find_docker_compose() -> list[str]:
    if shutil.which('docker'):
        probe = subprocess.run(
            ['docker', 'compose', 'version'],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return ['docker', 'compose']
    return []


def docker_postgres_service() -> str:
    try:
        from deployment_env import read_env  # noqa: WPS433
    except ImportError:
        return 'postgres'
    return (read_env('DOCKER_SERVICE_POSTGRES', 'postgres') or 'postgres').strip() or 'postgres'


def should_use_docker_exec(host: str, runtime: str, service: str | None = None) -> bool:
    if (runtime or '').strip().lower() != 'docker':
        return False
    value = (host or '').strip()
    if _is_loopback(value):
        return False
    expected = (service or docker_postgres_service()).strip()
    return value == expected or value.lower() == expected.lower()


def _compose_exec_cmd(root: Path, service: str, inner: list[str], *, env_pairs: list[str] | None = None) -> list[str]:
    compose = _find_docker_compose()
    if not compose:
        raise BackupError(t('db_backup_docker_compose_missing'))
    docker_dir = root / 'core' / 'deployment' / 'docker'
    cmd = [*compose]
    for name in ('docker-compose.yml', 'docker-compose.postgres.yml'):
        path = docker_dir / name
        if path.is_file():
            cmd.extend(['-f', str(path)])
    cmd.extend(['exec', '-T'])
    for pair in env_pairs or []:
        cmd.extend(['-e', pair])
    cmd.extend([service, *inner])
    return cmd


def run_docker_pg_tool(
    root: Path,
    inner: list[str],
    *,
    user: str,
    password: str,
    service: str | None = None,
    stdout_path: Path | None = None,
    stdin_path: Path | None = None,
    timeout: float = DUMP_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    name = service or docker_postgres_service()
    env_pairs = [f'PGUSER={user}', f'PGPASSWORD={password}', 'PGCLIENTENCODING=UTF8']
    cmd = _compose_exec_cmd(root, name, inner, env_pairs=env_pairs)
    stdin_handle = None
    stdout_handle = None
    try:
        if stdin_path is not None:
            stdin_handle = stdin_path.open('rb')
        if stdout_path is not None:
            stdout_handle = stdout_path.open('wb')
        raw = subprocess.run(
            cmd,
            stdin=stdin_handle,
            stdout=stdout_handle if stdout_handle is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    finally:
        if stdin_handle is not None:
            stdin_handle.close()
        if stdout_handle is not None:
            stdout_handle.close()
    stderr = _decode_pg_output(raw.stderr)
    stdout = '' if stdout_path is not None else _decode_pg_output(raw.stdout)
    return subprocess.CompletedProcess(args=cmd[:6], returncode=raw.returncode, stdout=stdout, stderr=stderr)


def dump_postgres(root: Path, section: dict[str, str], dest: Path, *, runtime: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        '-d', section['name'],
        '-Fc',
        '--no-owner',
        '--no-acl',
    ]
    if should_use_docker_exec(section.get('host') or '', runtime):
        result = run_docker_pg_tool(
            root,
            ['pg_dump', *args],
            user=section.get('user') or '',
            password=section.get('password') or '',
            stdout_path=dest,
        )
    else:
        host = normalize_host(section.get('host') or '')
        port = str(section.get('port') or '')
        local_args = ['-h', host]
        if port:
            local_args.extend(['-p', port])
        local_args.extend(
            ['-d', section['name'], '-Fc', '--no-owner', '--no-acl', '-f', str(dest)],
        )
        result = run_local_pg_tool(
            root,
            'pg_dump',
            local_args,
            env=_pg_env(section.get('user') or '', section.get('password') or '', host),
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        if detail:
            print(detail, file=sys.stderr)
        raise BackupError(t('db_backup_tool_failed', label='pg_dump', code=result.returncode))
    if not dest.is_file() or dest.stat().st_size == 0:
        raise BackupError(t('db_backup_empty_dump', alias=section['alias']))


def restore_postgres(root: Path, section: dict[str, str], source: Path, *, runtime: str) -> None:
    if not source.is_file():
        raise BackupError(t('db_backup_dump_missing', path=str(source)))
    args = ['--clean', '--if-exists', '--no-owner', '--no-acl', '-d', section['name']]
    if should_use_docker_exec(section.get('host') or '', runtime):
        result = run_docker_pg_tool(
            root,
            ['pg_restore', *args],
            user=section.get('user') or '',
            password=section.get('password') or '',
            stdin_path=source,
        )
    else:
        host = normalize_host(section.get('host') or '')
        port = str(section.get('port') or '')
        local_args = ['--clean', '--if-exists', '--no-owner', '--no-acl', '-h', host]
        if port:
            local_args.extend(['-p', port])
        local_args.extend(['-d', section['name'], str(source)])
        result = run_local_pg_tool(
            root,
            'pg_restore',
            local_args,
            env=_pg_env(section.get('user') or '', section.get('password') or '', host),
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        if detail:
            print(detail, file=sys.stderr)
        raise BackupError(t('db_backup_tool_failed', label='pg_restore', code=result.returncode))


def _resolve_client_tool(names: tuple[str, ...]) -> Path | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def dump_mysql(section: dict[str, str], dest: Path) -> None:
    binary = _resolve_client_tool(('mysqldump', 'mysqldump.exe'))
    if binary is None:
        raise BackupError(t('db_backup_mysql_tool_missing'))
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'MYSQL_PWD': section.get('password') or ''}
    args = [
        str(binary),
        '-h', normalize_host(section.get('host') or ''),
        '-u', section.get('user') or 'root',
        '--single-transaction',
        '--routines',
        '--triggers',
        section['name'],
    ]
    port = str(section.get('port') or '').strip()
    if port:
        args[3:3] = ['-P', port]
    raw = subprocess.run(args, capture_output=True, check=False, env=env, timeout=DUMP_TIMEOUT_SEC)
    if raw.returncode != 0:
        print(_decode_pg_output(raw.stderr) or _decode_pg_output(raw.stdout), file=sys.stderr)
        raise BackupError(t('db_backup_tool_failed', label='mysqldump', code=raw.returncode))
    dest.write_bytes(raw.stdout or b'')
    if dest.stat().st_size == 0:
        raise BackupError(t('db_backup_empty_dump', alias=section['alias']))


def restore_mysql(section: dict[str, str], source: Path) -> None:
    binary = _resolve_client_tool(('mysql', 'mysql.exe'))
    if binary is None:
        raise BackupError(t('db_backup_mysql_tool_missing'))
    if not source.is_file():
        raise BackupError(t('db_backup_dump_missing', path=str(source)))
    env = {**os.environ, 'MYSQL_PWD': section.get('password') or ''}
    args = [
        str(binary),
        '-h', normalize_host(section.get('host') or ''),
        '-u', section.get('user') or 'root',
        section['name'],
    ]
    port = str(section.get('port') or '').strip()
    if port:
        args[3:3] = ['-P', port]
    raw = subprocess.run(
        args,
        input=source.read_bytes(),
        capture_output=True,
        check=False,
        env=env,
        timeout=DUMP_TIMEOUT_SEC,
    )
    if raw.returncode != 0:
        print(_decode_pg_output(raw.stderr) or _decode_pg_output(raw.stdout), file=sys.stderr)
        raise BackupError(t('db_backup_tool_failed', label='mysql', code=raw.returncode))


def dump_mssql(section: dict[str, str], dest: Path) -> None:
    binary = _resolve_client_tool(('sqlcmd', 'sqlcmd.exe'))
    if binary is None:
        raise BackupError(t('db_backup_mssql_tool_missing'))
    dest.parent.mkdir(parents=True, exist_ok=True)
    server = normalize_host(section.get('host') or '')
    port = str(section.get('port') or '').strip()
    if port:
        server = f'{server},{port}'
    query = f"BACKUP DATABASE [{section['name']}] TO DISK = N'{dest}' WITH INIT, COPY_ONLY"
    args = [str(binary), '-S', server, '-U', section.get('user') or '', '-Q', query]
    env = {**os.environ, 'SQLCMDPASSWORD': section.get('password') or ''}
    raw = subprocess.run(args, capture_output=True, check=False, env=env, timeout=DUMP_TIMEOUT_SEC)
    if raw.returncode != 0:
        print(_decode_pg_output(raw.stderr) or _decode_pg_output(raw.stdout), file=sys.stderr)
        raise BackupError(t('db_backup_tool_failed', label='sqlcmd', code=raw.returncode))
    if not dest.is_file():
        raise BackupError(t('db_backup_empty_dump', alias=section['alias']))


def restore_mssql(section: dict[str, str], source: Path) -> None:
    binary = _resolve_client_tool(('sqlcmd', 'sqlcmd.exe'))
    if binary is None:
        raise BackupError(t('db_backup_mssql_tool_missing'))
    if not source.is_file():
        raise BackupError(t('db_backup_dump_missing', path=str(source)))
    server = normalize_host(section.get('host') or '')
    port = str(section.get('port') or '').strip()
    if port:
        server = f'{server},{port}'
    query = (
        f"ALTER DATABASE [{section['name']}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
        f"RESTORE DATABASE [{section['name']}] FROM DISK = N'{source}' WITH REPLACE; "
        f"ALTER DATABASE [{section['name']}] SET MULTI_USER"
    )
    args = [str(binary), '-S', server, '-U', section.get('user') or '', '-Q', query]
    env = {**os.environ, 'SQLCMDPASSWORD': section.get('password') or ''}
    raw = subprocess.run(args, capture_output=True, check=False, env=env, timeout=DUMP_TIMEOUT_SEC)
    if raw.returncode != 0:
        print(_decode_pg_output(raw.stderr) or _decode_pg_output(raw.stdout), file=sys.stderr)
        raise BackupError(t('db_backup_tool_failed', label='sqlcmd', code=raw.returncode))


def dump_section(root: Path, section: dict[str, str], dest: Path, *, runtime: str) -> None:
    engine = section['engine']
    if engine == 'postgresql':
        dump_postgres(root, section, dest, runtime=runtime)
        return
    if engine == 'sqlite':
        copy_sqlite(sqlite_db_path(root, section), dest)
        return
    if engine == 'mysql':
        dump_mysql(section, dest)
        return
    if engine == 'mssql':
        dump_mssql(section, dest)
        return
    raise BackupError(t('db_backup_engine_unsupported', engine=engine))


def restore_section(root: Path, section: dict[str, str], source: Path, *, runtime: str) -> None:
    engine = section['engine']
    if engine == 'postgresql':
        restore_postgres(root, section, source, runtime=runtime)
        return
    if engine == 'sqlite':
        copy_sqlite(source, sqlite_db_path(root, section))
        return
    if engine == 'mysql':
        restore_mysql(section, source)
        return
    if engine == 'mssql':
        restore_mssql(section, source)
        return
    raise BackupError(t('db_backup_engine_unsupported', engine=engine))
