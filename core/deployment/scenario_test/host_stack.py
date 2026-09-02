"""Живой стек на хосте: те же start_*.py, свои порты и каталоги, без служб ОС."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping

from install_redis import redis_server_path
from lifecycle.modules.catalog import ModuleCatalog
from nginx_foreground import nginx_paths
from postgres_common import postgres_bin
from project_layout import ensure_dir
from scenario_test.isolation import sanitized_os_env
from scenario_test.jupyter_overlay import apply_jupyter_isolation
from scenario_test.matrix import ScenarioSpec, spec_env_overrides
from scenario_test.stack import (
    SCENARIO_DB_NAME,
    posix,
    write_databases_yaml,
    write_nginx_conf,
    write_runtime_env,
)


class HostBinaryMissing(Exception):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(name)


def venv_python(root: Path) -> Path:
    win = root / 'virtual_env' / 'python' / 'Scripts' / 'python.exe'
    if win.is_file():
        return win
    unix = root / 'virtual_env' / 'python' / 'bin' / 'python'
    if unix.is_file():
        return unix
    raise HostBinaryMissing('python')


def disabled_modules_csv(project_root: Path) -> str:
    catalog = ModuleCatalog.from_env(project_root, environ={})
    return ','.join(catalog.enabled_names())


def build_host_env(
    *,
    project_root: Path,
    run_dir: Path,
    spec: ScenarioSpec,
    ports: Mapping[str, int],
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = sanitized_os_env()
    tmp_dir = ensure_dir(run_dir / 'tmp')
    overlay = spec_env_overrides(spec)
    overlay.update({
        'ERGO_DATABASES_YAML': str(run_dir / 'databases.yaml'),
        'REDIS_HOST': '127.0.0.1',
        'REDIS_PORT': str(int(ports['redis'])),
        'API_HOST': '127.0.0.1',
        'API_PORT': str(int(ports['api'])),
        'API_ALLOWED_HOSTS': 'localhost,127.0.0.1',
        'MEDIA_API_BIND_HOST': '127.0.0.1',
        'MEDIA_API_BIND_PORT': str(int(ports['media'])),
        'MEDIA_STORAGE_PATH': str(run_dir / 'media'),
        'ERGO_LOGS_DIR': str(run_dir / 'logs'),
        'API_JUPYTER_BIND_HOST': '127.0.0.1',
        'API_JUPYTER_BIND_PORT': str(int(ports['jupyter'])),
        'ERGO_SEARCH_ENABLED': 'false',
        'DOCKER_ENABLED': 'false',
        'ERGO_ENV': 'development',
        'ERGO_DOCKER_REQUIRES_SETUP': '0',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUTF8': '1',
        'TMP': str(tmp_dir),
        'TEMP': str(tmp_dir),
        'TMPDIR': str(tmp_dir),
    })
    if spec.disable_all_modules:
        overlay['DISABLED_MODULES'] = disabled_modules_csv(project_root)
    if extra:
        overlay.update({key: str(value) for key, value in extra.items()})
    env.update(overlay)
    path_entries = [str(project_root), str(project_root / 'core' / 'api')]
    env['PYTHONPATH'] = os.pathsep.join(path_entries)
    apply_jupyter_isolation(env, run_dir)
    return env


def write_host_artifacts(
    *,
    run_dir: Path,
    spec: ScenarioSpec,
    ports: Mapping[str, int],
    project_root: Path,
    api_secret: str,
    jwt_secret: str,
    media_internal_key: str,
    meili_key: str,
    jupyter_token: str,
    module_name: str = '',
) -> None:
    yaml_kwargs: dict = {
        'redis_host': '127.0.0.1',
        'redis_port': int(ports['redis']),
    }
    if spec.use_postgres:
        write_databases_yaml(
            run_dir / 'databases.yaml',
            db='postgres',
            db_host='127.0.0.1',
            db_port=int(ports['postgres']),
            **yaml_kwargs,
        )
    elif spec.use_mysql:
        write_databases_yaml(
            run_dir / 'databases.yaml',
            db='mysql',
            db_host='127.0.0.1',
            db_port=int(ports['mysql']),
            **yaml_kwargs,
        )
    elif spec.use_mssql:
        write_databases_yaml(
            run_dir / 'databases.yaml',
            db='mssql',
            db_host='127.0.0.1',
            db_port=int(ports['mssql']),
            **yaml_kwargs,
        )
    else:
        write_databases_yaml(
            run_dir / 'databases.yaml',
            db='sqlite',
            sqlite_path=run_dir / 'scenario.sqlite3',
            **yaml_kwargs,
        )
    extra = spec_env_overrides(spec)
    extra.update({
        'ERGO_DATABASES_YAML': str(run_dir / 'databases.yaml'),
        'REDIS_HOST': '127.0.0.1',
        'REDIS_PORT': str(int(ports['redis'])),
        'MEDIA_API_BIND_PORT': str(int(ports['media'])),
        'API_JUPYTER_TOKEN': jupyter_token,
        'ERGO_SEARCH_ENABLED': 'false',
        'DOCKER_ENABLED': 'false',
    })
    if spec.disable_all_modules:
        extra['DISABLED_MODULES'] = disabled_modules_csv(project_root)
    write_runtime_env(
        run_dir / '.env',
        ports=ports,
        api_secret=api_secret,
        jwt_secret=jwt_secret,
        media_internal_key=media_internal_key,
        meili_key=meili_key,
        jupyter_token=jupyter_token,
        extra=extra,
    )
    if spec.use_nginx:
        write_nginx_conf(
            run_dir / 'nginx.conf',
            api_port=int(ports['api']),
            nginx_port=int(ports['nginx']),
            api_upstream='127.0.0.1',
            media_upstream='127.0.0.1',
            jupyter_upstream='127.0.0.1',
            jupyter_port=int(ports['jupyter']),
            media_port=int(ports['media']),
            module_runtime=spec.module_runtime,
            microservice_modules=module_name,
            module_port=str(int(ports['module'])) if module_name else '',
        )


def _popen_kwargs() -> dict:
    kwargs: dict = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    return kwargs


def spawn_python(
    *,
    project_root: Path,
    script: str,
    env: Mapping[str, str],
    log_path: Path,
    extra_args: tuple[str, ...] = (),
) -> subprocess.Popen[str]:
    python = venv_python(project_root)
    handle = log_path.open('ab')
    proc = subprocess.Popen(
        [str(python), str(project_root / script), *extra_args],
        cwd=str(project_root),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        **_popen_kwargs(),
    )
    proc._log_handle = handle  # type: ignore[attr-defined]
    return proc


def run_django(
    *,
    project_root: Path,
    env: Mapping[str, str],
    command: str,
    args: tuple[str, ...],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    python = venv_python(project_root)
    api_dir = project_root / 'core' / 'api'
    quoted = ', '.join(repr(item) for item in args)
    code = (
        'import sys; sys.path.insert(0, r"{api}"); '
        'from commands.base import PoetryCommand; '
        'raise SystemExit(PoetryCommand.for_django("{cmd}").run({args}))'
    ).format(api=str(api_dir), cmd=command, args=quoted)
    return subprocess.run(
        [str(python), '-c', code],
        cwd=str(api_dir),
        env=dict(env),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        check=False,
    )


def require_host_binaries(project_root: Path, spec: ScenarioSpec) -> None:
    if not venv_python(project_root).is_file():
        raise HostBinaryMissing('python')
    if spec.use_postgres and not postgres_bin(project_root, 'pg_ctl').is_file():
        raise HostBinaryMissing('postgres')
    if spec.use_redis and not redis_server_path(project_root).is_file():
        raise HostBinaryMissing('redis')
    if spec.use_nginx:
        _nginx_dir, exe, _conf = nginx_paths()
        if not exe.is_file():
            raise HostBinaryMissing('nginx')


def start_throwaway_postgres(project_root: Path, data_dir: Path, port: int, log_file: Path) -> None:
    initdb = postgres_bin(project_root, 'initdb')
    pg_ctl = postgres_bin(project_root, 'pg_ctl')
    if not initdb.is_file() or not pg_ctl.is_file():
        raise HostBinaryMissing('postgres')
    ensure_dir(data_dir)
    ensure_dir(log_file.parent)
    pwfile = data_dir.parent / 'pwfile'
    pwfile.write_text('admin\n', encoding='utf-8')
    try:
        init = subprocess.run(
            [
                str(initdb),
                '-D',
                str(data_dir),
                '-U',
                'postgres',
                '-A',
                'scram-sha-256',
                '--pwfile',
                str(pwfile),
                '-E',
                'UTF8',
                '--locale=C',
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        pwfile.unlink(missing_ok=True)
    if init.returncode != 0:
        raise RuntimeError(init.stderr or init.stdout or 'initdb failed')
    conf = data_dir / 'postgresql.conf'
    text = conf.read_text(encoding='utf-8') if conf.is_file() else ''
    lines = [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith(('listen_addresses', 'port', 'logging_collector'))
    ]
    lines.extend([
        "listen_addresses = '127.0.0.1'",
        f'port = {int(port)}',
        'logging_collector = off',
    ])
    conf.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    hba = data_dir / 'pg_hba.conf'
    if hba.is_file():
        hba.write_text(
            hba.read_text(encoding='utf-8')
            + '\nhost all all 127.0.0.1/32 scram-sha-256\n',
            encoding='utf-8',
        )
    start = subprocess.run(
        [
            str(pg_ctl),
            'start',
            '-D',
            str(data_dir),
            '-l',
            str(log_file),
            '-o',
            f'-p {int(port)} -h 127.0.0.1',
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if start.returncode != 0:
        raise RuntimeError('pg_ctl start failed')
    createdb = postgres_bin(project_root, 'createdb')
    env = os.environ.copy()
    env['PGPASSWORD'] = 'admin'
    ready_ok = False
    for _ in range(30):
        ready = subprocess.run(
            [
                str(postgres_bin(project_root, 'pg_isready')),
                '-h',
                '127.0.0.1',
                '-p',
                str(int(port)),
                '-U',
                'postgres',
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if ready.returncode == 0:
            ready_ok = True
            break
        time.sleep(1)
    if not ready_ok:
        raise RuntimeError('throwaway postgres did not become ready')
    db = subprocess.run(
        [
            str(createdb),
            '-h',
            '127.0.0.1',
            '-p',
            str(int(port)),
            '-U',
            'postgres',
            SCENARIO_DB_NAME,
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if db.returncode != 0 and 'already exists' not in (db.stderr or '').lower():
        raise RuntimeError(db.stderr or db.stdout or 'createdb failed')


def stop_throwaway_postgres(project_root: Path, data_dir: Path) -> None:
    pg_ctl = postgres_bin(project_root, 'pg_ctl')
    if not pg_ctl.is_file() or not (data_dir / 'PG_VERSION').is_file():
        return
    subprocess.run(
        [str(pg_ctl), 'stop', '-D', str(data_dir), '-m', 'fast', '-w'],
        capture_output=True,
        timeout=40,
        check=False,
    )


def start_throwaway_redis(project_root: Path, run_dir: Path, port: int, log_path: Path) -> subprocess.Popen[str]:
    server = redis_server_path(project_root)
    if not server.is_file():
        raise HostBinaryMissing('redis')
    data = ensure_dir(run_dir / 'redis')
    handle = log_path.open('ab')
    proc = subprocess.Popen(
        [
            str(server),
            '--port',
            str(int(port)),
            '--bind',
            '127.0.0.1',
            '--dir',
            str(data),
            '--dbfilename',
            'dump.rdb',
            '--save',
            '',
            '--appendonly',
            'no',
            '--protected-mode',
            'no',
        ],
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        **_popen_kwargs(),
    )
    proc._log_handle = handle  # type: ignore[attr-defined]
    return proc


def write_host_nginx_prefix(
    *,
    project_root: Path,
    run_dir: Path,
    ports: Mapping[str, int],
) -> Path:
    prefix = ensure_dir(run_dir / 'nginx_prefix')
    conf_dir = ensure_dir(prefix / 'conf')
    ensure_dir(prefix / 'conf.d')
    ensure_dir(prefix / 'logs')
    ensure_dir(prefix / 'temp')
    nginx_dir, _exe, _main = nginx_paths()
    mime_src = nginx_dir / 'conf' / 'mime.types'
    if mime_src.is_file():
        shutil.copyfile(mime_src, conf_dir / 'mime.types')
    else:
        (conf_dir / 'mime.types').write_text('types { text/html html; }\n', encoding='utf-8')
    default = (run_dir / 'nginx.conf').read_text(encoding='utf-8')
    dist = posix(project_root / 'core' / 'client' / 'dist')
    static_api = posix(run_dir / 'static_api')
    logs = posix(run_dir / 'logs')
    nginx_port = int(ports['nginx'])
    default = default.replace('/usr/share/nginx/html', dist)
    default = default.replace('/usr/share/nginx/static/', static_api.rstrip('/') + '/')
    default = default.replace('/var/log/ergo', logs)
    default = default.replace(f'listen {nginx_port};', f'listen 127.0.0.1:{nginx_port};')
    (prefix / 'conf.d' / 'default.conf').write_text(default, encoding='utf-8')
    (conf_dir / 'nginx.conf').write_text(
        (
            'worker_processes 1;\n'
            'error_log logs/error.log;\n'
            'pid logs/nginx.pid;\n'
            'events { worker_connections 128; }\n'
            'http {\n'
            '    include mime.types;\n'
            '    default_type application/octet-stream;\n'
            '    client_body_temp_path temp/client_body;\n'
            '    proxy_temp_path temp/proxy;\n'
            '    include ../conf.d/default.conf;\n'
            '}\n'
        ),
        encoding='utf-8',
    )
    return prefix


def start_throwaway_nginx(prefix: Path) -> subprocess.Popen[str]:
    _nginx_dir, exe, _conf = nginx_paths()
    if not exe.is_file():
        raise HostBinaryMissing('nginx')
    handle = (prefix / 'logs' / 'nginx-start.log').open('ab')
    proc = subprocess.Popen(
        [str(exe), '-p', str(prefix), '-c', 'conf/nginx.conf', '-g', 'daemon off;'],
        cwd=str(prefix),
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        **_popen_kwargs(),
    )
    proc._log_handle = handle  # type: ignore[attr-defined]
    return proc


def close_proc(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    handle = getattr(proc, '_log_handle', None)
    if proc.poll() is None:
        if sys.platform == 'win32' and proc.pid:
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            proc.wait(timeout=8)
        except (subprocess.TimeoutExpired, OSError):
            pass
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass
