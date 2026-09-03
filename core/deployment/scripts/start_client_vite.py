"""Общий запуск Vite dev для start_client_if_dev / start_client_if_enabled."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEPLOYMENT_DIR = SCRIPTS_DIR.parent
PROJECT_ROOT = DEPLOYMENT_DIR.parent.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from log_env import client_dev_log_level  # noqa: E402
from project_layout import npm_exe, npm_root_dir, prepend_client_cli_path  # noqa: E402


def run_vite_dev() -> int:
    """Запускает `npm run dev -w @ergo-ms/core-client`. Возвращает код выхода."""
    npm_cmd = str(npm_exe(PROJECT_ROOT))
    if not Path(npm_cmd).is_file():
        npm_cmd = 'npm.cmd' if os.name == 'nt' else 'npm'

    npm_root = npm_root_dir(PROJECT_ROOT)
    env = os.environ.copy()
    prepend_client_cli_path(env, PROJECT_ROOT)

    log_level = client_dev_log_level()
    return subprocess.call(
        [
            npm_cmd,
            'run',
            'dev',
            '-w',
            '@ergo-ms/core-client',
            '--',
            '--logLevel',
            log_level,
        ],
        cwd=str(npm_root),
        env=env,
    )
