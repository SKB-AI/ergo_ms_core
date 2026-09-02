"""Проверка bridge_manifest.yaml и MICROSERVICE_MODULES."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPTS_DIR.parent.parent.parent
MODULES_DIR = PROJECT_ROOT / 'modules'


def _parse_csv(raw: str) -> list[str]:
    return [p.strip() for p in (raw or '').split(',') if p.strip()]


def _load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        import yaml
    except ImportError:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except OSError:
        return None
    return data if isinstance(data, dict) else None


def find_manifest_violations() -> list[str]:
    """
    Ошибки:
    - битый/пустой manifest, если файл есть;
    - модуль из MICROSERVICE_MODULES без manifest (если env задан).
    """
    violations: list[str] = []
    required = _parse_csv(os.environ.get('MICROSERVICE_MODULES', ''))
    if not MODULES_DIR.is_dir():
        return violations

    existing: set[str] = set()
    for child in sorted(MODULES_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith('.'):
            continue
        path = child / 'api' / 'bridge_manifest.yaml'
        if not path.is_file():
            continue
        existing.add(child.name)
        data = _load_yaml(path)
        rel = str(path.relative_to(PROJECT_ROOT)).replace('\\', '/')
        if data is None:
            violations.append(f'{rel}: invalid or unreadable YAML')
            continue
        service = str(data.get('service') or '').strip()
        ops = data.get('ops')
        if not service:
            violations.append(f'{rel}: missing service')
        if ops is not None and not isinstance(ops, list):
            violations.append(f'{rel}: ops must be a list')

    for name in required:
        if name not in existing:
            violations.append(
                f'modules/{name}/api/bridge_manifest.yaml: required for MICROSERVICE_MODULES'
            )
    return violations
