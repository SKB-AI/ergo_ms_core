"""
Запуск Meilisearch для VS Code / ergoms start-meilisearch-dev.

При ERGO_SEARCH_ENABLED=true:
- нет процесса — старт portable + поток логов;
- уже запущен portable — только логи; закрытие терминала останавливает процесс;
- служба ОС — только логи, процесс не трогаем.
При ERGO_SEARCH_ENABLED=false: выход без сообщений.
"""

from __future__ import annotations

import atexit
import signal
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
from deployment_env import PROJECT_ROOT, is_search_enabled  # noqa: E402
from dev_session import is_managed_service  # noqa: E402
from install_meilisearch import (  # noqa: E402
    cmd_start,
    cmd_stop,
    is_installed,
    meilisearch_log_file,
    ping_meilisearch,
)
from log_env import log_file_path  # noqa: E402
from nginx_foreground import _configure_stdio_utf8, tail_log_files  # noqa: E402
from service_names import MEILISEARCH  # noqa: E402

MEILISEARCH_LINUX_SERVICE = f'{MEILISEARCH}.service'


def is_meilisearch_managed_service() -> bool:
    return is_managed_service(
        windows_name=MEILISEARCH,
        linux_name=MEILISEARCH_LINUX_SERVICE,
    )


def meilisearch_log_tail_paths() -> list[Path]:
    candidates = [
        log_file_path('MEILISEARCH', PROJECT_ROOT),
        meilisearch_log_file(PROJECT_ROOT),
    ]
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _tail_owned_session(*, fresh_start: bool) -> int:
    """Поток логов Meilisearch с остановкой portable-процесса при выходе из терминала."""
    session_owned = True

    def _cleanup() -> None:
        nonlocal session_owned
        if not session_owned:
            return
        cmd_stop(PROJECT_ROOT)
        session_owned = False

    def _handle_signal(signum: int, _frame: object) -> None:
        _cleanup()
        raise SystemExit(128 + signum if signum else 0)

    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    atexit.register(_cleanup)

    message_key = (
        'meilisearch_started_tail' if fresh_start else 'meilisearch_already_running_tail'
    )
    print(format_console('info', t(message_key)))
    try:
        return tail_log_files(
            meilisearch_log_tail_paths(),
            service='Meilisearch',
            process_keeps_running=False,
        )
    except KeyboardInterrupt:
        return 0
    finally:
        atexit.unregister(_cleanup)
        _cleanup()


def main() -> int:
    if not is_search_enabled():
        return 0

    _configure_stdio_utf8()

    if not is_installed(PROJECT_ROOT):
        print(format_console('error', t('meilisearch_not_installed_hint')))
        return 1

    if is_meilisearch_managed_service():
        print(format_console('info', t('meilisearch_os_service_terminal')))
        return tail_log_files(
            meilisearch_log_tail_paths(),
            service='Meilisearch',
            process_keeps_running=True,
        )

    fresh_start = False
    if not ping_meilisearch(PROJECT_ROOT):
        code = cmd_start(PROJECT_ROOT)
        if code != 0:
            return code
        fresh_start = True

    return _tail_owned_session(fresh_start=fresh_start)


if __name__ == '__main__':
    raise SystemExit(main())
