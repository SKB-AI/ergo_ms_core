"""
Общие пути, детект службы и операции с кластером portable PostgreSQL.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
SCRIPTS_DIR = DEPLOYMENT_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402

DOWNLOAD_USER_AGENT = 'ergoms/1.0 (PostgreSQL installer)'
DOWNLOAD_TIMEOUT_SEC = 600
DEFAULT_PORT = 5432
# Portable всегда на отдельном порту (не пересекается с типичным системным 5432).
PORTABLE_DEFAULT_PORT = 5433
DEFAULT_BIND = '127.0.0.1'
DEFAULT_DB_NAME = 'ergo_ms'
DEFAULT_DB_USER = 'postgres'
DEFAULT_DB_PASSWORD = 'admin'
OUR_SERVICE_WINDOWS = 'ergo_ms_postgres'
OUR_SERVICE_LINUX = 'ergo_ms_postgres'
DEFAULT_SERVICE_DISPLAY_NAME = 'Ergo MS - PostgreSQL'
DEFAULT_SERVICE_RESTART_DELAY_MS = 5000
# Knobs postgresql.conf из env/postgres.env (POSTGRES_* → ключ conf).
# Дефолт: 4 ядра / 8 потоков, 16 ГБ RAM, SSD.
_CONF_SETTING_ENV_KEYS = (
    ('POSTGRES_MAX_CONNECTIONS', 'max_connections'),
    ('POSTGRES_SHARED_BUFFERS', 'shared_buffers'),
    ('POSTGRES_WORK_MEM', 'work_mem'),
    ('POSTGRES_MAINTENANCE_WORK_MEM', 'maintenance_work_mem'),
    ('POSTGRES_EFFECTIVE_CACHE_SIZE', 'effective_cache_size'),
    ('POSTGRES_RANDOM_PAGE_COST', 'random_page_cost'),
    ('POSTGRES_EFFECTIVE_IO_CONCURRENCY', 'effective_io_concurrency'),
    ('POSTGRES_MAX_WORKER_PROCESSES', 'max_worker_processes'),
    ('POSTGRES_MAX_PARALLEL_WORKERS', 'max_parallel_workers'),
    ('POSTGRES_MAX_PARALLEL_WORKERS_PER_GATHER', 'max_parallel_workers_per_gather'),
    ('POSTGRES_MAX_PARALLEL_MAINTENANCE_WORKERS', 'max_parallel_maintenance_workers'),
    ('POSTGRES_MAX_WAL_SIZE', 'max_wal_size'),
    ('POSTGRES_WAL_COMPRESSION', 'wal_compression'),
)
_DEFAULT_CONF_SETTINGS = {
    'max_connections': '100',
    'shared_buffers': '4GB',
    'work_mem': '32MB',
    'maintenance_work_mem': '1GB',
    'effective_cache_size': '12GB',
    'random_page_cost': '1.1',
    'effective_io_concurrency': '200',
    'max_worker_processes': '8',
    'max_parallel_workers': '8',
    'max_parallel_workers_per_gather': '4',
    'max_parallel_maintenance_workers': '4',
    'max_wal_size': '2GB',
    'wal_compression': 'lz4',
}
PG_FTP_SOURCE = 'https://ftp.postgresql.org/pub/source/'
EDB_WINDOWS_URL = (
    'https://get.enterprisedb.com/postgresql/'
    'postgresql-{version}-1-windows-x64-binaries.zip'
)


def _read_postgres_env(name: str, default: str = '') -> str:
    """POSTGRES_* из .env + env/*.env (в т.ч. env/postgres.env)."""
    try:
        from deployment_env import read_env  # noqa: WPS433
    except ImportError:
        return default
    value = read_env(name, default)
    return (value or default).strip() or default


def our_service_windows(root: Path | None = None) -> str:
    explicit = _read_postgres_env('POSTGRES_SERVICE_WINDOWS', '')
    if explicit:
        return explicit
    from service_names import names_from_root

    return names_from_root(root).postgres


def our_service_linux(root: Path | None = None) -> str:
    explicit = _read_postgres_env('POSTGRES_SERVICE_LINUX', '')
    if explicit:
        return explicit
    from service_names import names_from_root

    return names_from_root(root).postgres


def service_display_name(root: Path | None = None) -> str:
    _ = root
    return _read_postgres_env('POSTGRES_SERVICE_DISPLAY_NAME', DEFAULT_SERVICE_DISPLAY_NAME)


def service_restart_delay_ms(root: Path | None = None) -> int:
    _ = root
    raw = _read_postgres_env(
        'POSTGRES_SERVICE_RESTART_DELAY_MS',
        str(DEFAULT_SERVICE_RESTART_DELAY_MS),
    )
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_SERVICE_RESTART_DELAY_MS


def load_portable_conf_settings(root: Path | None = None) -> dict[str, str]:
    """Параметры нагрузки для postgresql.conf (env/postgres.env)."""
    settings = dict(_DEFAULT_CONF_SETTINGS)
    for env_key, conf_key in _CONF_SETTING_ENV_KEYS:
        value = _read_postgres_env(env_key, settings[conf_key])
        if value:
            settings[conf_key] = value
    if root is not None:
        settings = _sanitize_wal_compression(root, settings)
    return settings


def _pg_config_configure(root: Path) -> str:
    binary = postgres_bin(root, 'pg_config')
    if not binary.is_file():
        return ''
    try:
        result = subprocess.run(
            [str(binary), '--configure'],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return result.stdout or ''


def _sanitize_wal_compression(root: Path, settings: dict[str, str]) -> dict[str, str]:
    value = (settings.get('wal_compression') or '').strip().lower()
    if value in ('', 'pglz', 'on', 'off'):
        return settings
    flags = _pg_config_configure(root)
    built = {
        'lz4': '--with-lz4' in flags,
        'zstd': '--with-zstd' in flags,
    }
    if built.get(value):
        return settings
    fallback = 'pglz'
    print(
        format_console(
            'warning',
            t('postgres_wal_compression_fallback', value=value, fallback=fallback),
        )
    )
    adjusted = dict(settings)
    adjusted['wal_compression'] = fallback
    return adjusted


def postgres_packages_dir(root: Path) -> Path:
    return root / 'virtual_env' / 'packages' / 'postgres'


def postgres_bin_dir(root: Path) -> Path:
    base = postgres_packages_dir(root)
    # EDB zip → pgsql/bin; после установки копируем в packages/postgres/bin
    direct = base / 'bin'
    if direct.is_dir():
        return direct
    nested = base / 'pgsql' / 'bin'
    if nested.is_dir():
        return nested
    return direct


def _exe(name: str) -> str:
    return f'{name}.exe' if platform.system().lower() == 'windows' else name


def postgres_bin(root: Path, name: str) -> Path:
    return postgres_bin_dir(root) / _exe(name)


def postgres_data_dir(root: Path) -> Path:
    return postgres_packages_dir(root) / 'data'


def postgres_version_file(root: Path) -> Path:
    return postgres_packages_dir(root) / 'VERSION'


def postgres_listen_port_file(root: Path) -> Path:
    return postgres_packages_dir(root) / 'PORT'


def write_portable_listen_port(root: Path, port: int) -> None:
    base = postgres_packages_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    postgres_listen_port_file(root).write_text(f'{int(port)}\n', encoding='utf-8')


def read_portable_listen_port(root: Path) -> int | None:
    marker = postgres_listen_port_file(root)
    if not marker.is_file():
        return None
    text = marker.read_text(encoding='utf-8').strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def resolve_portable_listen_port(
    root: Path,
    *,
    cli_port: int | None = None,
    alongside_system: bool = False,
) -> int:
    """Порт прослушивания portable: CLI → databases.yaml default.port → 5433."""
    _ = alongside_system
    if cli_port is not None:
        return int(cli_port)
    defaults = load_db_defaults(root)
    try:
        return int(defaults.get('port') or PORTABLE_DEFAULT_PORT)
    except ValueError:
        return PORTABLE_DEFAULT_PORT


def resolve_portable_bind(root: Path | None = None) -> str:
    """Адрес прослушивания portable: databases.yaml default.host → 127.0.0.1."""
    if root is None:
        root = PROJECT_ROOT
    defaults = load_db_defaults(root)
    host = (defaults.get('host') or DEFAULT_BIND).strip() or DEFAULT_BIND
    if host.lower() in {'localhost', '::1'}:
        return DEFAULT_BIND
    return host


def is_installed(root: Path) -> bool:
    return postgres_bin(root, 'postgres').is_file() and postgres_bin(root, 'pg_ctl').is_file()


def read_installed_version(root: Path) -> str | None:
    marker = postgres_version_file(root)
    if marker.is_file():
        text = marker.read_text(encoding='utf-8').strip()
        if text:
            return text
    postgres = postgres_bin(root, 'postgres')
    if not postgres.is_file():
        return None
    try:
        result = subprocess.run(
            [str(postgres), '--version'],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    match = re.search(r'(\d+\.\d+)', (result.stdout or '') + (result.stderr or ''))
    return match.group(1) if match else None


def _download_once(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': DOWNLOAD_USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SEC) as response:
        destination.write_bytes(response.read())
    if destination.stat().st_size < 1024:
        destination.unlink(missing_ok=True)
        raise RuntimeError(t('postgres_download_too_small', url=url))


def _download_with_curl(url: str, destination: Path) -> bool:
    curl_exe = shutil.which('curl')
    if not curl_exe:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                curl_exe, '-L', '--fail', '--retry', '3', '--retry-delay', '2',
                '-A', DOWNLOAD_USER_AGENT, '-o', str(destination), url,
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return (
        result.returncode == 0
        and destination.is_file()
        and destination.stat().st_size >= 1024
    )


def _download(url: str, destination: Path, *, fallback_urls: tuple[str, ...] = ()) -> None:
    last_error: Exception | None = None
    for candidate in (url, *fallback_urls):
        print(t('postgres_downloading', url=candidate))
        try:
            _download_once(candidate, destination)
            return
        except Exception as exc:
            last_error = exc
            print(format_console('warning', t('postgres_download_failed', exc=exc)))
            destination.unlink(missing_ok=True)
        if _download_with_curl(candidate, destination):
            return
    raise RuntimeError(
        t('postgres_download_archive_failed', last_error=last_error)
    ) from last_error


def resolve_latest_version() -> str:
    request = urllib.request.Request(
        PG_FTP_SOURCE,
        headers={'User-Agent': DOWNLOAD_USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        html = response.read().decode('utf-8', errors='replace')
    versions: list[tuple[int, int]] = []
    for match in re.finditer(r'v(\d+)\.(\d+)/', html):
        versions.append((int(match.group(1)), int(match.group(2))))
    if not versions:
        raise RuntimeError(t('postgres_latest_version_failed'))
    major, minor = max(versions)
    return f'{major}.{minor}'


def _ensure_layout(root: Path) -> dict[str, Path]:
    base = postgres_packages_dir(root)
    paths = {
        'base': base,
        'bin': base / 'bin',
        'data': base / 'data',
        'logs': base / 'logs',
        'run': base / 'run',
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def has_system_postgresql_service() -> bool:
    system = platform.system().lower()
    our_windows = our_service_windows()
    our_linux = our_service_linux()
    if system == 'windows':
        try:
            result = subprocess.run(
                [
                    'powershell.exe', '-NoProfile', '-Command',
                    "Get-Service -ErrorAction SilentlyContinue | "
                    "Where-Object { $_.Name -like 'postgresql*' "
                    f"-and $_.Name -ne '{our_windows}' }} | "
                    "Select-Object -ExpandProperty Name",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        names = [line.strip() for line in (result.stdout or '').splitlines() if line.strip()]
        return bool(names)

    try:
        result = subprocess.run(
            ['systemctl', 'list-units', '--type=service', '--all', '--no-legend', '--plain', 'postgresql*'],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return False
    for line in (result.stdout or '').splitlines():
        unit = line.split()[0] if line.strip() else ''
        if not unit.endswith('.service'):
            continue
        short = unit[:-8]
        if short == our_linux or short.startswith(f'{our_linux}.'):
            continue
        if short.startswith('postgresql'):
            return True
    return False


def _parse_simple_yaml_section(text: str, section: str) -> dict[str, str]:
    """Минимальный разбор секции databases.yaml без PyYAML."""
    lines = text.splitlines()
    in_databases = False
    in_section = False
    section_indent = -1
    result: dict[str, str] = {}
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        stripped = raw.strip()
        if stripped == 'databases:':
            in_databases = True
            in_section = False
            continue
        if not in_databases:
            continue
        if indent == 2 and stripped.endswith(':'):
            name = stripped[:-1].strip()
            in_section = name == section
            section_indent = indent
            continue
        if in_section and indent > section_indent and ':' in stripped:
            key, _, value = stripped.partition(':')
            value = value.strip().strip('"').strip("'")
            result[key.strip()] = value
        elif in_section and indent <= section_indent and stripped.endswith(':'):
            break
    return result


def load_db_defaults(root: Path) -> dict[str, str]:
    path = root / 'databases.yaml'
    defaults = {
        'name': DEFAULT_DB_NAME,
        'user': DEFAULT_DB_USER,
        'password': DEFAULT_DB_PASSWORD,
        'host': 'localhost',
        'port': str(PORTABLE_DEFAULT_PORT),
    }
    if not path.is_file():
        return defaults
    text = path.read_text(encoding='utf-8')
    section = _parse_simple_yaml_section(text, 'default')
    for key in ('name', 'user', 'password', 'host', 'port'):
        if section.get(key):
            defaults[key] = section[key]
    return defaults


def load_extra_db_sections(root: Path) -> list[dict[str, str]]:
    path = root / 'databases.yaml'
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8')
    extras: list[dict[str, str]] = []
    for section_name in ('celery', 'celery_worker', 'celery_beat'):
        section = _parse_simple_yaml_section(text, section_name)
        if section.get('name') and section.get('user') and section.get('password'):
            extras.append({
                'name': section['name'],
                'user': section['user'],
                'password': section['password'],
            })
    return extras


def _patch_conf_file(path: Path, replacements: dict[str, str]) -> None:
    if not path.is_file():
        return
    lines = path.read_text(encoding='utf-8').splitlines()
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.lstrip()
        replaced = False
        for key, value in replacements.items():
            if (
                stripped.startswith(f'{key} ')
                or stripped.startswith(f'{key}=')
                or stripped.startswith(f'{key}\t')
                or stripped.startswith(f'#{key}')
            ):
                out.append(f"{key} = {value}")
                seen.add(key)
                replaced = True
                break
        if not replaced:
            out.append(line)
    for key, value in replacements.items():
        if key not in seen:
            out.append(f'{key} = {value}')
    path.write_text('\n'.join(out) + '\n', encoding='utf-8')


def _configure_cluster(root: Path, port: int, bind: str) -> None:
    from log_env import log_basename, resolve_logs_dir

    data = postgres_data_dir(root)
    logs_dir = resolve_logs_dir(root)
    logs_dir.mkdir(parents=True, exist_ok=True)
    replacements = {
        'listen_addresses': f"'{bind}'",
        'port': str(port),
        'logging_collector': 'on',
        'log_directory': f"'{logs_dir.as_posix()}'",
        'log_filename': f"'{log_basename('POSTGRES', root)}'",
    }
    for conf_key, value in load_portable_conf_settings(root).items():
        # Строковые размеры (128MB) — без кавычек; числа — как есть.
        replacements[conf_key] = value
    _patch_conf_file(data / 'postgresql.conf', replacements)
    write_portable_listen_port(root, port)
    hba = data / 'pg_hba.conf'
    if hba.is_file():
        content = hba.read_text(encoding='utf-8')
        if '127.0.0.1/32' not in content:
            content += '\nhost all all 127.0.0.1/32 scram-sha-256\n'
            content += 'host all all ::1/128 scram-sha-256\n'
            hba.write_text(content, encoding='utf-8')


def effective_portable_port(root: Path, port: int | None = None) -> int:
    if port is not None:
        return int(port)
    stored = read_portable_listen_port(root)
    if stored is not None:
        return stored
    return resolve_portable_listen_port(root)


def _cluster_owner_ids(root: Path) -> tuple[int, int] | None:
    """Владелец проекта: initdb/pg_ctl нельзя запускать от root."""
    if os.name == 'nt' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return None
    uid, gid = Path(root).stat().st_uid, Path(root).stat().st_gid
    if uid == 0:
        return None
    return uid, gid


def _chown_postgres_packages(root: Path) -> None:
    owner = _cluster_owner_ids(root)
    if owner is None:
        return
    uid, gid = owner
    pkg = postgres_packages_dir(root)
    if not pkg.is_dir():
        return
    try:
        from lifecycle.host.privilege import restore_project_ownership  # noqa: WPS433
        restore_project_ownership(root, pkg)
        return
    except ImportError:
        pass
    for dirpath, dirnames, filenames in os.walk(pkg):
        os.chown(dirpath, uid, gid, follow_symlinks=False)
        for name in (*dirnames, *filenames):
            os.chown(Path(dirpath) / name, uid, gid, follow_symlinks=False)


def _run_pg(
    root: Path,
    tool: str,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    binary = postgres_bin(root, tool)
    if not binary.is_file():
        raise RuntimeError(t('postgres_tool_not_found', tool=tool, binary=binary))
    full_env = {**os.environ, **(env or {})}
    run_kwargs: dict[str, object] = {}
    owner = _cluster_owner_ids(root)
    if owner is not None:
        _chown_postgres_packages(root)
        run_kwargs['user'] = owner[0]
        run_kwargs['group'] = owner[1]
        try:
            import pwd

            pw = pwd.getpwuid(owner[0])
            full_env['HOME'] = pw.pw_dir
            full_env['USER'] = pw.pw_name
            full_env['LOGNAME'] = pw.pw_name
        except KeyError:
            pass
    result = subprocess.run(
        [str(binary), *args],
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
        timeout=120,
        **run_kwargs,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        if detail:
            print(detail, file=sys.stderr)
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def _initdb_if_needed(root: Path, user: str, password: str, port: int, bind: str) -> None:
    data = postgres_data_dir(root)
    marker = data / 'PG_VERSION'
    if marker.is_file():
        _configure_cluster(root, port, bind)
        return

    paths = _ensure_layout(root)
    pwfile = paths['run'] / 'pwfile'
    pwfile.write_text(password + '\n', encoding='utf-8')
    try:
        print(t('postgres_initdb_arrow'))
        _run_pg(
            root,
            'initdb',
            [
                '-D', str(data),
                '-U', user,
                '-A', 'scram-sha-256',
                '--pwfile', str(pwfile),
                '-E', 'UTF8',
                '--locale=C',
            ],
        )
    finally:
        pwfile.unlink(missing_ok=True)
    _configure_cluster(root, port, bind)
    print(format_console('ok', t('postgres_cluster_initialized', data=data)))


def is_server_running(root: Path) -> bool:
    data = postgres_data_dir(root)
    if not (data / 'PG_VERSION').is_file():
        return False
    try:
        result = _run_pg(root, 'pg_ctl', ['status', '-D', str(data)], check=False)
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def start_server(root: Path) -> None:
    if is_server_running(root):
        return
    data = postgres_data_dir(root)
    log_file = postgres_packages_dir(root) / 'logs' / 'pg_ctl.log'
    print(t('postgres_starting_pg_ctl'))
    _run_pg(
        root,
        'pg_ctl',
        ['start', '-D', str(data), '-l', str(log_file), '-w', '-t', '60'],
    )


def stop_server(root: Path) -> None:
    if not is_server_running(root):
        return
    data = postgres_data_dir(root)
    _run_pg(root, 'pg_ctl', ['stop', '-D', str(data), '-m', 'fast', '-w'], check=False)


def _psql_env(user: str, password: str) -> dict[str, str]:
    return {
        'PGUSER': user,
        'PGPASSWORD': password,
        'PGHOST': '127.0.0.1',
    }


def ensure_databases(root: Path, port: int | None = None) -> None:
    defaults = load_db_defaults(root)
    port_i = effective_portable_port(root, port)
    user = defaults['user']
    password = defaults['password']
    dbname = defaults['name']
    env = _psql_env(user, password)

    def _exec_sql(sql: str, database: str = 'postgres') -> None:
        _run_pg(
            root,
            'psql',
            ['-v', 'ON_ERROR_STOP=1', '-p', str(port_i), '-d', database, '-c', sql],
            env=env,
        )

    # wait for accept
    for _ in range(30):
        try:
            _run_pg(
                root,
                'psql',
                ['-p', str(port_i), '-d', 'postgres', '-c', 'SELECT 1'],
                env=env,
            )
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            time.sleep(1)
    else:
        raise RuntimeError(t('postgres_not_accepting'))

    check = _run_pg(
        root,
        'psql',
        [
            '-p', str(port_i), '-d', 'postgres', '-tAc',
            f"SELECT 1 FROM pg_database WHERE datname = '{dbname}'",
        ],
        env=env,
        check=False,
    )
    if '1' not in (check.stdout or ''):
        print(t('postgres_create_database_arrow', dbname=dbname))
        _exec_sql(f'CREATE DATABASE "{dbname}" OWNER "{user}"')
        print(format_console('ok', t('postgres_db_created', dbname=dbname)))
    else:
        print(format_console('skip', t('postgres_db_exists', dbname=dbname)))

    _ensure_pg_trgm_in_core(_exec_sql, root, dbname)

    for extra in load_extra_db_sections(root):
        ename = extra['name']
        euser = extra['user']
        epass = extra['password']
        role_check = _run_pg(
            root,
            'psql',
            ['-p', str(port_i), '-d', 'postgres', '-tAc', f"SELECT 1 FROM pg_roles WHERE rolname = '{euser}'"],
            env=env,
            check=False,
        )
        if '1' not in (role_check.stdout or ''):
            safe_pass = epass.replace("'", "''")
            _exec_sql(f"CREATE ROLE \"{euser}\" LOGIN PASSWORD '{safe_pass}'")
        db_check = _run_pg(
            root,
            'psql',
            ['-p', str(port_i), '-d', 'postgres', '-tAc', f"SELECT 1 FROM pg_database WHERE datname = '{ename}'"],
            env=env,
            check=False,
        )
        if '1' not in (db_check.stdout or ''):
            _exec_sql(f'CREATE DATABASE "{ename}" OWNER "{euser}"')
            print(format_console('ok', t('postgres_db_created', dbname=ename)))

    print_db_access_summary(root, port=port_i)


def _pg_trgm_control_file(root: Path) -> Path | None:
    base = postgres_packages_dir(root)
    candidates = (
        base / 'share' / 'extension' / 'pg_trgm.control',
        base / 'share' / 'postgresql' / 'extension' / 'pg_trgm.control',
        base / 'pgsql' / 'share' / 'extension' / 'pg_trgm.control',
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _ensure_pg_trgm_in_core(exec_sql, root: Path, dbname: str) -> None:
    """pg_trgm в схеме core: Django не кладёт public в search_path."""
    if _pg_trgm_control_file(root) is None:
        return
    exec_sql(
        'CREATE SCHEMA IF NOT EXISTS core; '
        'DO $$ BEGIN '
        "IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN "
        'ALTER EXTENSION pg_trgm SET SCHEMA core; '
        'ELSE CREATE EXTENSION pg_trgm SCHEMA core; '
        'END IF; END $$;',
        database=dbname,
    )
    print(format_console('ok', t('postgres_pg_trgm_in_core')))


def print_db_access_summary(root: Path, port: int | None = None) -> None:
    """Печатает имя БД и учётные данные (databases.yaml default или встроенные)."""
    defaults = load_db_defaults(root)
    port_i = effective_portable_port(root, port)
    host = defaults.get('host') or 'localhost'
    print(format_console(
        'info',
        t(
            'postgres_db_access_summary',
            name=defaults['name'],
            host=host,
            port=port_i,
            user=defaults['user'],
            password=defaults['password'],
        ),
    ))


def ping_postgres(root: Path, port: int | None = None, timeout_sec: float = 5.0) -> bool:
    if not is_installed(root):
        return False
    defaults = load_db_defaults(root)
    port_i = effective_portable_port(root, port)
    env = _psql_env(defaults['user'], defaults['password'])
    try:
        result = subprocess.run(
            [
                str(postgres_bin(root, 'psql')),
                '-p', str(port_i),
                '-d', 'postgres',
                '-tAc', 'SELECT 1',
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env={**os.environ, **env},
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and '1' in (result.stdout or '')

