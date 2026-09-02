"""
Запуск Redis для разработки без tail логов.

Используется перед прогревом кэшей и API, когда REDIS_ENABLED=true.
Терминал с логами — ergoms start-redis-dev (VS Code Redis Dev / Start All Services).
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
from deployment_env import PROJECT_ROOT, is_redis_enabled  # noqa: E402
from install_redis import is_installed, ping_redis  # noqa: E402
from nginx_foreground import _configure_stdio_utf8  # noqa: E402
from redis_dev import (  # noqa: E402
    is_redis_managed_service,
    read_dev_session_marker,
    read_redis_pid,
    start_redis_detached,
    write_dev_session_marker,
)


def ensure_redis_for_dev(*, quiet: bool = False) -> int:
    """
    Запускает Redis, если REDIS_ENABLED=true и процесс ещё не отвечает на ping.

    При REDIS_ENABLED=false возвращает 0 без действий.
    После detached-старта записывает dev-session marker для handoff в терминал.
    """
    if not is_redis_enabled():
        return 0

    if not is_installed(PROJECT_ROOT):
        print(format_console('error', t('redis_not_installed_hint')))
        return 1

    if is_redis_managed_service(PROJECT_ROOT):
        if not quiet:
            print(format_console('info', t('redis_os_service_running')))
        return 0

    def _claim_dev_session(*, source: str) -> None:
        # Marker нужен start-redis-dev: без него portable Redis считается «внешним»
        # и закрытие терминала VS Code его не остановит.
        if read_dev_session_marker(PROJECT_ROOT) is None:
            write_dev_session_marker(
                PROJECT_ROOT,
                pid=read_redis_pid(PROJECT_ROOT),
                source=source,
            )

    if ping_redis(PROJECT_ROOT, timeout_sec=0.3):
        _claim_dev_session(source='warmup-adopt')
        if not quiet:
            print(format_console('info', t('redis_already_started')))
        return 0

    if not start_redis_detached(PROJECT_ROOT, quiet=quiet):
        return 1

    _claim_dev_session(source='warmup')
    return 0


def main() -> int:
    if not is_redis_enabled():
        return 0

    _configure_stdio_utf8()
    return ensure_redis_for_dev()


if __name__ == '__main__':
    raise SystemExit(main())
