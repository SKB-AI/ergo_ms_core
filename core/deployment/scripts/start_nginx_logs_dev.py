"""Хвост журналов nginx для VS Code Logs: All Services / ergoms logs ergo_ms_nginx.

GNU tail -F по двум пустым файлам рисует только заголовки ``==> path <==``.
Этот скрипт пишет строки как ``[nginx-error.log] …`` и объясняет пустые файлы.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = SCRIPTS_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from log_env import log_file_path  # noqa: E402
from nginx_foreground import (  # noqa: E402
    _configure_stdio_utf8,
    is_nginx_running,
    nginx_log_tail_paths,
    nginx_paths,
    tail_log_files,
)


def _initial_lines() -> int:
    if len(sys.argv) < 2:
        return 500
    try:
        return max(1, int(sys.argv[1]))
    except ValueError:
        return 500


def _existing_files(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.is_file()]


def _all_empty(paths: list[Path]) -> bool:
    return bool(paths) and all(path.stat().st_size == 0 for path in paths)


def main() -> int:
    _configure_stdio_utf8()
    lines = _initial_lines()
    paths = _existing_files(nginx_log_tail_paths())
    if not paths:
        logs_dir = log_file_path('NGINX_ERROR').parent
        print(format_console('error', t('svc_nginx_logs_missing', path=str(logs_dir))))
        print(format_console('warning', t('svc_nginx_logs_hint')))
        return 1

    print(format_console('info', t('svc_tail_nginx_logs', lines=lines)))
    if _all_empty(paths):
        nginx_dir, exe, _ = nginx_paths()
        running = exe.is_file() and is_nginx_running(nginx_dir, exe)
        empty_key = 'nginx_logs_empty_idle' if running else 'nginx_logs_empty_stopped'
        print(format_console('warning', t(empty_key)))
        print(format_console('info', t('nginx_logs_empty_hint')))

    return tail_log_files(
        paths,
        service='nginx',
        process_keeps_running=True,
        initial_lines=lines,
    )


if __name__ == '__main__':
    raise SystemExit(main())
