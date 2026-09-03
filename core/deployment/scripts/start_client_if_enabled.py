"""
Запуск Vite dev-сервера для VS Code / ergoms start-client-dev.

При NGINX_ENABLED=false: ждёт API ready, затем npm run dev.
При NGINX_ENABLED=true: выход без сообщений (клиент отдаётся через nginx).
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

from deployment_env import is_nginx_enabled  # noqa: E402
from lifecycle.host_process_guard import refuse_unwanted_core_service  # noqa: E402
from lifecycle.host_profile import SERVICE_CLIENT  # noqa: E402
from start_client_vite import run_vite_dev  # noqa: E402
from wait_api_ready import wait_for_api_ready  # noqa: E402

_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent


def main() -> int:
    if is_nginx_enabled():
        return 0
    refused = refuse_unwanted_core_service(
        SERVICE_CLIENT,
        message_key='host_refuses_core_client',
        project_root=_PROJECT_ROOT,
    )
    if refused:
        return refused
    wait_for_api_ready()
    return run_vite_dev()


if __name__ == '__main__':
    raise SystemExit(main())
