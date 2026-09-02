"""
Запуск JupyterLab для VS Code / ergoms start-jupyter-dev.

При ERGO_JUPYTER отличном от none вызывает start_jupyter.py.
При ERGO_JUPYTER=none: выход без сообщений.
Явная команда ergoms start-jupyter по-прежнему запускает Jupyter даже при none.
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

from deployment_env import PROJECT_ROOT, is_jupyter_enabled  # noqa: E402


def main() -> int:
    if not is_jupyter_enabled():
        return 0
    api_scripts = PROJECT_ROOT / 'core' / 'api' / 'scripts'
    if str(api_scripts) not in sys.path:
        sys.path.insert(0, str(api_scripts))
    from start_jupyter import main as run_jupyter  # noqa: WPS433

    return run_jupyter()


if __name__ == '__main__':
    raise SystemExit(main())
