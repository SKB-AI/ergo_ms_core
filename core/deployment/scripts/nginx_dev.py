"""
Dev-lifecycle nginx: marker сессии, foreground-запуск, остановка при закрытии терминала.

Используется start_nginx_if_enabled и start_client_if_dev (терминал VS Code).
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
from deployment_env import PROJECT_ROOT, resolve_public_host  # noqa: E402
from dev_session import (  # noqa: E402
    clear_dev_session_marker as _clear_marker,
    dev_session_marker_path as _marker_path,
    is_managed_service,
    read_dev_session_marker as _read_marker,
    run_foreground_with_session,
    write_dev_session_marker as _write_marker,
)
from nginx_foreground import (  # noqa: E402
    _configure_stdio_utf8,
    _print_client_hint,
    is_nginx_running,
    nginx_paths,
    read_env_port,
    tail_log_files,
)

NGINX_WINDOWS_SERVICE = 'ergo_ms_nginx'
NGINX_LINUX_SERVICE = 'ergo_ms_nginx.service'


def nginx_run_dir(root: Path) -> Path:
    nginx_dir, _, _ = nginx_paths()
    _ = root
    return nginx_dir / 'run'


def dev_session_marker_path(root: Path) -> Path:
    return _marker_path(nginx_run_dir(root))


def read_dev_session_marker(root: Path):
    return _read_marker(nginx_run_dir(root))


def write_dev_session_marker(root: Path, *, pid: int | None, source: str) -> None:
    _write_marker(nginx_run_dir(root), pid=pid, source=source)


def clear_dev_session_marker(root: Path) -> None:
    _clear_marker(nginx_run_dir(root))


def is_nginx_managed_service(root: Path) -> bool:
    _ = root
    return is_managed_service(windows_name=NGINX_WINDOWS_SERVICE, linux_name=NGINX_LINUX_SERVICE)


def _linux_nginx_unit_installed() -> bool:
    """Unit systemd установлен (даже если сейчас inactive)."""
    if os.name == 'nt':
        return False
    result = subprocess.run(
        ['systemctl', 'cat', NGINX_LINUX_SERVICE],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _listen_port_needs_privilege(port: str) -> bool:
    try:
        return int(str(port).strip()) < 1024
    except (TypeError, ValueError):
        return True


def _remove_stale_pidfile(nginx_dir: Path) -> None:
    (nginx_dir / 'logs' / 'nginx.pid').unlink(missing_ok=True)


def wait_nginx_stopped(nginx_dir: Path, exe: Path, *, timeout_sec: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not is_nginx_running(nginx_dir, exe):
            return True
        time.sleep(0.2)
    return False


def _force_stop_nginx_process(nginx_dir: Path, exe: Path) -> None:
    if os.name == 'nt':
        subprocess.run(
            ['taskkill', '/F', '/IM', exe.name],
            capture_output=True,
            check=False,
        )
        return

    subprocess.run(
        ['pkill', '-f', str(exe)],
        capture_output=True,
        check=False,
    )


def stop_nginx_for_dev(root: Path, *, quiet: bool = False) -> bool:
    """
    Останавливает portable nginx (nginx -s quit). Службу ОС не трогает.

    Returns True, если nginx больше не запущен.
    """
    if is_nginx_managed_service(root):
        return True

    if not quiet:
        _configure_stdio_utf8()

    nginx_dir, exe, main_conf = nginx_paths()

    if not is_nginx_running(nginx_dir, exe):
        _remove_stale_pidfile(nginx_dir)
        return True

    if not quiet:
        print(format_console('info', t('stopping_nginx')))

    main_conf_arg = str(main_conf).replace('\\', '/') if os.name == 'nt' else str(main_conf)
    subprocess.run(
        [str(exe), '-s', 'quit', '-c', main_conf_arg],
        cwd=str(nginx_dir),
        capture_output=True,
        check=False,
    )
    time.sleep(0.5)

    _remove_stale_pidfile(nginx_dir)

    if is_nginx_running(nginx_dir, exe):
        if not quiet:
            print(format_console('warning', t('nginx_quit_force')))
        _force_stop_nginx_process(nginx_dir, exe)
        _remove_stale_pidfile(nginx_dir)
        wait_nginx_stopped(nginx_dir, exe, timeout_sec=5.0)

    still_running = is_nginx_running(nginx_dir, exe)
    if not still_running and not quiet:
        print(format_console('ok', t('nginx_stopped')))
    elif still_running and not quiet:
        print(format_console('error', t('nginx_stop_incomplete')))
    return not still_running


def run_nginx_foreground() -> int:
    _configure_stdio_utf8()
    nginx_dir, exe, main_conf = nginx_paths()
    if not exe.is_file():
        print(format_console('error', t('nginx_not_installed_hint')))
        return 1

    public_host = resolve_public_host()
    port = read_env_port()
    url = f'http://{public_host}'
    if port not in ('80', '443'):
        url = f'{url}:{port}'

    from nginx_foreground import nginx_log_tail_paths  # noqa: WPS433

    access_paths = nginx_log_tail_paths()

    if is_nginx_managed_service(PROJECT_ROOT):
        print(format_console('info', t('nginx_os_service_no_control')))
        _print_client_hint(url)
        return tail_log_files(access_paths, service='nginx', process_keeps_running=True)

    # Порт <1024 без root/capability: foreground из VS Code падает с Permission denied.
    # Если unit установлен — нужен ergoms start-nginx, а не start-nginx-dev.
    if (
        os.name != 'nt'
        and _listen_port_needs_privilege(port)
        and _linux_nginx_unit_installed()
    ):
        print(format_console('error', t('nginx_privileged_port_use_service', port=port)))
        return 1

    marker = read_dev_session_marker(PROJECT_ROOT)

    if is_nginx_running(nginx_dir, exe):
        if marker is None:
            print(format_console(
                'info',
                t('nginx_external_running'),
            ))
            _print_client_hint(url)
            return tail_log_files(access_paths, service='nginx', process_keeps_running=True)

        print(format_console('info', t('nginx_handoff_to_terminal')))
        stop_nginx_for_dev(PROJECT_ROOT)
        clear_dev_session_marker(PROJECT_ROOT)
        if not wait_nginx_stopped(nginx_dir, exe, timeout_sec=5.0):
            print(format_console('error', t('nginx_stop_before_fg_failed')))
            return 1
    elif marker is not None:
        clear_dev_session_marker(PROJECT_ROOT)

    test = subprocess.run(
        [str(exe), '-t', '-c', str(main_conf)],
        cwd=str(nginx_dir),
        check=False,
    )
    if test.returncode != 0:
        from log_env import log_file_path  # noqa: WPS433

        error_log = log_file_path('NGINX_ERROR', PROJECT_ROOT)
        print(format_console('error', t('nginx_config_check_failed_see', error_log=error_log)))
        return test.returncode

    print(format_console(
        'info',
        t('starting_nginx_foreground'),
    ))
    _print_client_hint(url)

    main_conf_arg = str(main_conf).replace('\\', '/') if os.name == 'nt' else str(main_conf)
    run_dir = nginx_run_dir(PROJECT_ROOT)

    def _launch() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [str(exe), '-c', main_conf_arg, '-g', 'daemon off;'],
            cwd=str(nginx_dir),
        )

    return run_foreground_with_session(
        run_dir=run_dir,
        stop_quiet=lambda: stop_nginx_for_dev(PROJECT_ROOT, quiet=True),
        launch=_launch,
    )
