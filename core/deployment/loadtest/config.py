"""Конфигурация host для loadtest (чтение .env, без записи)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from env_file_loader import load_project_env  # noqa: E402


def project_root_from_here() -> Path:
    # loadtest → deployment → core → project
    return Path(__file__).resolve().parent.parent.parent.parent


def load_env(root: Path) -> dict[str, str]:
    merged = dict(load_project_env(root))
    # Процессное окружение перекрывает файлы (удобно для CI / разового запуска).
    for key, value in os.environ.items():
        if value:
            merged[key] = value
    return merged


def resolve_api_host(env: dict[str, str], *, explicit: str | None = None) -> str:
    if explicit:
        return explicit.rstrip('/')
    host = (env.get('API_HOST') or 'localhost').strip() or 'localhost'
    port = (env.get('API_PORT') or '8000').strip() or '8000'
    return f'http://{host}:{port}'


def default_report_path(root: Path) -> Path:
    return root / 'logs' / 'loadtest' / 'report.html'
