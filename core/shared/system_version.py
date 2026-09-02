"""Версия системы ERGO MS — общий модуль для api и media_api."""

from __future__ import annotations

import os
from pathlib import Path

SYSTEM_VERSION = '3.0.0'
SYSTEM_VERSION_DISPLAY = '3.0'


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def get_system_version() -> str:
    value = os.environ.get('VERSION', '').strip()
    if value:
        return value

    env_path = _project_root() / '.env'
    if env_path.is_file():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, raw = line.partition('=')
            if key.strip() == 'VERSION':
                parsed = raw.strip().strip('"').strip("'")
                if parsed:
                    return parsed

    return SYSTEM_VERSION


def get_system_version_display() -> str:
    version = get_system_version()
    if version.endswith('.0'):
        return version[:-2]
    return version
