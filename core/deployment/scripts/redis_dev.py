"""
Dev-lifecycle Redis: marker сессии, foreground-запуск, остановка при закрытии терминала.

Используется ensure_redis_if_enabled (warmup) и start_redis_if_enabled (терминал VS Code).
"""

from __future__ import annotations

import argparse
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
from dev_session import (  # noqa: E402
    clear_dev_session_marker as _clear_marker,
    dev_session_marker_path as _marker_path,
    is_managed_service,
    read_dev_session_marker as _read_marker,
    run_foreground_with_session,
    write_dev_session_marker as _write_marker,
)
from install_redis import (  # noqa: E402
    ping_redis,
    redis_cli_auth_args,
    redis_cli_path,
    redis_conf_path,
    redis_packages_dir,
    redis_server_path,
    render_redis_conf,
    wait_redis_ready,
)
from log_env import log_file_path  # noqa: E402
from nginx_foreground import _configure_stdio_utf8  # noqa: E402

REDIS_WINDOWS_SERVICE = 'ergo_ms_redis'
REDIS_LINUX_SERVICE = 'ergo_ms_redis.service'
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def redis_run_dir(root: Path) -> Path:
    return redis_packages_dir(root) / 'run'


def redis_pidfile_path(root: Path) -> Path:
    return redis_packages_dir(root) / 'run' / 'redis.pid'


def read_redis_pid(root: Path) -> int | None:
    pidfile = redis_pidfile_path(root)
    if not pidfile.is_file():
        return None
    try:
        return int(pidfile.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None


def is_redis_managed_service(root: Path) -> bool:
    _ = root
    return is_managed_service(windows_name=REDIS_WINDOWS_SERVICE, linux_name=REDIS_LINUX_SERVICE)


def _redis_cli_endpoint(root: Path) -> tuple[str, str]:
    bind = '127.0.0.1'
    port = '6379'
    conf = redis_conf_path(root)
    if conf.is_file():
        for line in conf.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if stripped.startswith('bind '):
                bind = stripped.split()[1]
            elif stripped.startswith('port '):
                port = stripped.split()[1]
    return bind, port


def stop_redis_for_dev(root: Path, *, quiet: bool = False) -> bool:
    """
    Останавливает portable Redis (redis-cli shutdown). Службу ОС не трогает.

    Returns True, если Redis больше не отвечает на ping.
    """
    if is_redis_managed_service(root):
        return True

    if not quiet:
        _configure_stdio_utf8()

    if not ping_redis(root):
        _remove_stale_pidfile(root)
        return True

    cli = redis_cli_path(root)
    if cli.is_file():
        if not quiet:
            print(format_console('info', t('stopping_redis')))
        bind, port = _redis_cli_endpoint(root)
        auth = redis_cli_auth_args(root)
        subprocess.run(
            [str(cli), '-h', bind, '-p', port, *auth, 'shutdown'],
            capture_output=True,
            check=False,
        )
        time.sleep(0.5)

    _remove_stale_pidfile(root)

    if ping_redis(root):
        if not quiet:
            print(format_console('warning', t('redis_shutdown_force')))
        _force_stop_redis_process(root)

    still_running = ping_redis(root)
    if not still_running and not quiet:
        print(format_console('ok', t('redis_stopped')))
    return not still_running


def _remove_stale_pidfile(root: Path) -> None:
    redis_pidfile_path(root).unlink(missing_ok=True)


def _force_stop_redis_process(root: Path) -> None:
    if os.name == 'nt':
        subprocess.run(
            ['taskkill', '/F', '/IM', 'redis-server.exe'],
            capture_output=True,
            check=False,
        )
        return

    redis_dir = redis_packages_dir(root)
    subprocess.run(
        ['pkill', '-f', f'{redis_dir}/bin/redis-server'],
        capture_output=True,
        check=False,
    )


def portable_redis_process_running(root: Path) -> bool:
    pid = read_redis_pid(root)
    if pid is not None:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            pass
    if os.name == 'nt':
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq redis-server.exe', '/NH'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
        return 'redis-server.exe' in (result.stdout or '').lower()
    redis_dir = redis_packages_dir(root)
    result = subprocess.run(
        ['pgrep', '-f', f'{redis_dir}/bin/redis-server'],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _popen_redis_server(root: Path) -> subprocess.Popen[bytes]:
    server = redis_server_path(root)
    redis_dir = redis_packages_dir(root)
    conf_arg = 'conf\\redis.conf' if os.name == 'nt' else 'conf/redis.conf'
    kwargs: dict = {
        'args': [str(server), conf_arg],
        'cwd': str(redis_dir),
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
    }
    if os.name == 'nt':
        kwargs['creationflags'] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        )
    else:
        kwargs['start_new_session'] = True
        kwargs['close_fds'] = True
    return subprocess.Popen(**kwargs)


def start_redis_detached(root: Path, *, timeout_sec: float = 8.0, quiet: bool = False) -> bool:
    """
    Запускает portable redis-server так, чтобы он пережил конец прогрева кэшей.

    Не вызывает ergoms start-redis: на Windows это два PowerShell и redis-cli
    на каждую проверку готовности.
    """
    server = redis_server_path(root)
    conf = redis_conf_path(root)
    if not server.is_file() or not conf.is_file():
        print(format_console('error', t('redis_not_installed_hint')))
        return False

    if is_redis_managed_service(root):
        return True

    if ping_redis(root, timeout_sec=0.3):
        if not quiet:
            print(format_console('ok', t('redis_already_started')))
        return True

    if portable_redis_process_running(root):
        _force_stop_redis_process(root)
        time.sleep(0.1)
    _remove_stale_pidfile(root)

    render_redis_conf(root)

    if not quiet:
        print(format_console('info', t('arrow_starting', name='Redis')))

    try:
        _popen_redis_server(root)
    except OSError:
        print(format_console('error', t('redis_ping_failed_log', path=log_file_path('REDIS', root))))
        return False

    if wait_redis_ready(root, timeout_sec=timeout_sec):
        if not quiet:
            print(format_console('ok', t('redis_started_ok')))
        return True

    print(format_console('error', t('redis_ping_failed_log', path=log_file_path('REDIS', root))))
    return False


def run_redis_foreground(root: Path) -> int:
    """
    Запускает redis-server в foreground; при выходе останавливает только свою сессию.
    """
    server = redis_server_path(root)
    conf = redis_conf_path(root)
    redis_dir = redis_packages_dir(root)
    run_dir = redis_run_dir(root)

    if not server.is_file() or not conf.is_file():
        print(format_console('error', t('redis_not_installed_hint')))
        return 1

    if is_redis_managed_service(root):
        print(format_console('error', t('redis_os_service_use_stop')))
        return 1

    _configure_stdio_utf8()

    # Синхронизируем requirepass с databases.yaml перед стартом.
    render_redis_conf(root)

    if ping_redis(root):
        print(format_console('error', t('redis_already_running')))
        return 1

    print(format_console(
        'info',
        t('starting_redis_foreground'),
    ))

    conf_arg = 'conf/redis.conf' if os.name != 'nt' else 'conf\\redis.conf'

    def _launch() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [str(server), conf_arg, '--daemonize', 'no'],
            cwd=str(redis_dir),
        )

    return run_foreground_with_session(
        run_dir=run_dir,
        stop_quiet=lambda: stop_redis_for_dev(root, quiet=True),
        launch=_launch,
    )


def dev_session_marker_path(root: Path) -> Path:
    return _marker_path(redis_run_dir(root))


def read_dev_session_marker(root: Path):
    return _read_marker(redis_run_dir(root))


def write_dev_session_marker(root: Path, *, pid: int | None, source: str) -> None:
    _write_marker(redis_run_dir(root), pid=pid, source=source)


def clear_dev_session_marker(root: Path) -> None:
    _clear_marker(redis_run_dir(root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Redis dev lifecycle')
    parser.add_argument('--root', type=Path, default=_PROJECT_ROOT)
    parser.add_argument('--start', action='store_true', help='Start portable Redis detached')
    args = parser.parse_args(argv)
    if not args.start:
        parser.error('specify --start')
    _configure_stdio_utf8()
    return 0 if start_redis_detached(args.root.resolve()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
