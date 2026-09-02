"""Изоляция сценарного прогона от рабочего .env и databases.yaml."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_KEEP_ENV = frozenset({
    'PATH',
    'PATHEXT',
    'SYSTEMROOT',
    'WINDIR',
    'SYSTEMDRIVE',
    'COMSPEC',
    'USERNAME',
    'USERPROFILE',
    'HOMEDRIVE',
    'HOMEPATH',
    'USERDOMAIN',
    'USERDOMAIN_ROAMINGPROFILE',
    'APPDATA',
    'LOCALAPPDATA',
    'NUMBER_OF_PROCESSORS',
    'PROCESSOR_ARCHITECTURE',
    'PROCESSOR_IDENTIFIER',
    'PROCESSOR_LEVEL',
    'PROCESSOR_REVISION',
    'PROGRAMFILES',
    'PROGRAMFILES(X86)',
    'PROGRAMW6432',
    'PROGRAMDATA',
    'HOME',
    'USER',
    'LOGNAME',
    'SHELL',
    'LANG',
    'LANGUAGE',
    'LC_ALL',
    'LC_CTYPE',
    'LC_MESSAGES',
    'TZ',
    'TERM',
    'DISPLAY',
    'XDG_RUNTIME_DIR',
    'DOCKER_HOST',
    'DOCKER_CONTEXT',
    'DOCKER_TLS_VERIFY',
    'DOCKER_CERT_PATH',
    'DOCKER_CONFIG',
})

_WORKSPACE_CONFIG_FILES = ('.env', 'databases.yaml')


def sanitized_os_env() -> dict[str, str]:
    """Копия os.environ только с системными ключами, без ERGO_* и секретов проекта."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.upper() in _KEEP_ENV:
            env[key] = value
    return env


def workspace_config_fingerprint(root: Path) -> dict[str, str]:
    """SHA-256 рабочих конфигов. Пустой файл на диске даёт пустую строку."""
    result: dict[str, str] = {}
    for name in _WORKSPACE_CONFIG_FILES:
        path = root / name
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            result[name] = ''
    return result
