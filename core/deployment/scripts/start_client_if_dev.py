"""
Запуск Vite dev-сервера или nginx в foreground.

При NGINX_ENABLED=true клиент отдаётся через nginx; Vite (:8001) не нужен.
Закрытие терминала или Ctrl+C останавливает nginx сессии разработки.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEPLOYMENT_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from deployment_env import is_nginx_enabled  # noqa: E402
from lifecycle.host_process_guard import refuse_unwanted_core_service  # noqa: E402
from lifecycle.host_profile import SERVICE_CLIENT  # noqa: E402
from nginx_dev import run_nginx_foreground  # noqa: E402
from start_client_vite import run_vite_dev  # noqa: E402
from wait_api_ready import wait_for_api_ready  # noqa: E402

_PROJECT_ROOT = DEPLOYMENT_DIR.parent.parent


def main() -> int:
    wait_for_api_ready()
    if is_nginx_enabled():
        return run_nginx_foreground()
    refused = refuse_unwanted_core_service(
        SERVICE_CLIENT,
        message_key='host_refuses_core_client',
        project_root=_PROJECT_ROOT,
    )
    if refused:
        return refused
    return run_vite_dev()


if __name__ == '__main__':
    raise SystemExit(main())
