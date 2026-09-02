"""
Состав Django-стека в процессе module:<name>.

full — все apps ядра (как раньше). slim — платформенный минимум плюс extras из env и hook.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

PROFILE_FULL = 'full'
PROFILE_SLIM = 'slim'

PLATFORM_CORE_APPS: frozenset[str] = frozenset({
    'src.core.audit',
    'src.core.cms',
    'src.core.cms.adp',
    'src.core.integrations',
    'src.core.utils',
})

SLIM_CORE_URL_PREFIXES: tuple[str, ...] = ('system/',)

PROCESS_PROFILE_FILENAME = 'process_profile.yaml'


def parse_csv_paths(raw: str = '') -> frozenset[str]:
    return frozenset(item.strip() for item in (raw or '').split(',') if item.strip())


def process_role(environ: Mapping[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    return (env.get('ERGO_PROCESS_ROLE') or '').strip().lower()


def module_name_from_role(environ: Mapping[str, str] | None = None) -> str | None:
    role = process_role(environ)
    if not role.startswith('module:'):
        return None
    name = role.split(':', 1)[1].strip()
    return name or None


def process_profile_name(environ: Mapping[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    raw = (env.get('MODULE_PROCESS_PROFILE') or PROFILE_FULL).strip().lower()
    if raw == PROFILE_SLIM:
        return PROFILE_SLIM
    return PROFILE_FULL


def is_slim_module_process(environ: Mapping[str, str] | None = None) -> bool:
    return module_name_from_role(environ) is not None and process_profile_name(environ) == PROFILE_SLIM


def _hook_extra_apps(project_root: Path, module_name: str) -> frozenset[str]:
    path = project_root / 'modules' / module_name / 'api' / PROCESS_PROFILE_FILENAME
    if not path.is_file():
        return frozenset()
    try:
        import yaml  # noqa: WPS433
    except ImportError:
        return frozenset()
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError):
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    raw = data.get('core_apps')
    if isinstance(raw, str):
        return parse_csv_paths(raw)
    if isinstance(raw, list):
        return frozenset(str(item).strip() for item in raw if str(item).strip())
    return frozenset()


def extra_core_apps(
    project_root: Path | None,
    environ: Mapping[str, str] | None = None,
    module_name: str | None = None,
) -> frozenset[str]:
    env = environ if environ is not None else os.environ
    extras = set(parse_csv_paths(env.get('MODULE_PROCESS_CORE_EXTRA', '')))
    name = module_name or module_name_from_role(env)
    if project_root is not None and name:
        extras.update(_hook_extra_apps(Path(project_root), name))
    return frozenset(extras)


def allowed_core_apps(
    project_root: Path | None,
    environ: Mapping[str, str] | None = None,
) -> frozenset[str] | None:
    """None — не фильтровать (full / не module-процесс)."""
    if not is_slim_module_process(environ):
        return None
    allowed = set(PLATFORM_CORE_APPS)
    allowed.update(extra_core_apps(project_root, environ))
    return frozenset(allowed)


def filter_core_apps(
    apps: list[str],
    project_root: Path | None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    allowed = allowed_core_apps(project_root, environ)
    if allowed is None:
        return list(apps)
    kept: list[str] = []
    for app in apps:
        if app in allowed or any(app.startswith(f'{item}.') for item in allowed):
            kept.append(app)
    return kept


def allow_core_url_route(route: str, environ: Mapping[str, str] | None = None) -> bool:
    if not is_slim_module_process(environ):
        return True
    normalized = route.lstrip('/')
    return any(normalized == prefix or normalized.startswith(prefix) for prefix in SLIM_CORE_URL_PREFIXES)


def process_profile_fingerprint(
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = environ if environ is not None else os.environ
    extras = ','.join(sorted(extra_core_apps(project_root, env)))
    return (
        f'process_profile={process_profile_name(env)};'
        f'role={process_role(env)};'
        f'extra={extras}'
    )
