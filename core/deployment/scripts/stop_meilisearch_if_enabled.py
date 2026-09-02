"""
Остановка portable Meilisearch после закрытия терминала VS Code (stop-meilisearch-dev).

Службу ОС не трогает. При ERGO_SEARCH_ENABLED=false — тихий выход.
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

from deployment_env import PROJECT_ROOT, is_search_enabled  # noqa: E402
from dev_session import is_managed_service  # noqa: E402
from install_meilisearch import cmd_stop  # noqa: E402
from nginx_foreground import _configure_stdio_utf8  # noqa: E402
from service_names import MEILISEARCH  # noqa: E402

MEILISEARCH_LINUX_SERVICE = f'{MEILISEARCH}.service'


def main() -> int:
    if not is_search_enabled():
        return 0

    _configure_stdio_utf8()

    if is_managed_service(
        windows_name=MEILISEARCH,
        linux_name=MEILISEARCH_LINUX_SERVICE,
    ):
        return 0

    return cmd_stop(PROJECT_ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
