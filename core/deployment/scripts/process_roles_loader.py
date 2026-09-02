"""
Загрузка модульных process_roles.yaml для ergoms resource-usage.

Discovery: modules/<имя>/process_roles.yaml через ModuleCatalog.
Вне Django — только декларативный YAML, без импорта api модулей.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from lifecycle.modules.catalog import ModuleCatalog  # noqa: E402

PROCESS_ROLES_FILENAME = 'process_roles.yaml'


@dataclass(frozen=True)
class ProcessRoleWhen:
    cmdline_contains_any: tuple[str, ...] = ()
    cwd_contains_any: tuple[str, ...] = ()
    project_bound: bool = False


@dataclass(frozen=True)
class ProcessRoleRule:
    role_id: str
    module: str
    process_names: tuple[str, ...]
    when: tuple[ProcessRoleWhen, ...]
    reserve_host_budget: bool = False
    reserve_vram_mb: float = 0.0


def _warn(message: str) -> None:
    print(format_console('warning', message), file=sys.stderr)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                items.append(text)
        return tuple(items)
    return ()


def _parse_when_entry(raw: Any, *, module: str, role_id: str, path: Path) -> ProcessRoleWhen | None:
    if not isinstance(raw, dict):
        _warn(t('process_role_when_must_be_object', path=path, role_id=role_id, module=module))
        return None

    cmdline = _as_str_tuple(raw.get('cmdline_contains_any'))
    cwd = _as_str_tuple(raw.get('cwd_contains_any'))
    project_bound = bool(raw.get('project_bound', False))

    if not cmdline and not cwd and not project_bound:
        _warn(
            t('process_role_when_empty', path=path, role_id=role_id, module=module)
        )
        return None

    return ProcessRoleWhen(
        cmdline_contains_any=cmdline,
        cwd_contains_any=cwd,
        project_bound=project_bound,
    )


def _parse_role(raw: Any, *, module: str, path: Path) -> ProcessRoleRule | None:
    if not isinstance(raw, dict):
        _warn(t('process_roles_item_must_be_object', path=path, module=module))
        return None

    role_id = str(raw.get('id') or '').strip()
    if not role_id:
        _warn(t('process_role_missing_id', path=path, module=module))
        return None

    when_raw = raw.get('when')
    if not isinstance(when_raw, list) or not when_raw:
        _warn(t('process_role_needs_when', path=path, role_id=role_id, module=module))
        return None

    when_entries: list[ProcessRoleWhen] = []
    for entry in when_raw:
        parsed = _parse_when_entry(entry, module=module, role_id=role_id, path=path)
        if parsed is not None:
            when_entries.append(parsed)

    if not when_entries:
        _warn(t('process_role_no_valid_when', path=path, role_id=role_id, module=module))
        return None

    process_names = tuple(
        name.lower() for name in _as_str_tuple(raw.get('process_names'))
    )
    reserve_vram = 0.0
    raw_vram = raw.get('reserve_vram_mb')
    if raw_vram not in (None, ''):
        try:
            reserve_vram = max(0.0, float(raw_vram))
        except (TypeError, ValueError):
            reserve_vram = 0.0
    return ProcessRoleRule(
        role_id=role_id,
        module=module,
        process_names=process_names,
        when=tuple(when_entries),
        reserve_host_budget=bool(raw.get('reserve_host_budget', False)),
        reserve_vram_mb=reserve_vram,
    )


def _load_file(path: Path, *, module_dir_name: str) -> list[ProcessRoleRule]:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        _warn(t('yaml_read_failed', path=path, exc=exc))
        return []

    if not text.strip():
        _warn(t('yaml_file_empty', path=path))
        return []

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _warn(t('yaml_parse_error', path=path, exc=exc))
        return []

    if not isinstance(data, dict):
        _warn(t('yaml_root_must_be_object', path=path))
        return []

    module = str(data.get('module') or module_dir_name).strip() or module_dir_name
    roles_raw = data.get('roles')
    if not isinstance(roles_raw, list) or not roles_raw:
        _warn(t('process_roles_list_required', path=path))
        return []

    rules: list[ProcessRoleRule] = []
    for item in roles_raw:
        rule = _parse_role(item, module=module, path=path)
        if rule is not None:
            rules.append(rule)
    return rules


@lru_cache(maxsize=8)
def load_module_process_roles(project_root: str) -> tuple[ProcessRoleRule, ...]:
    """Загрузить правила из modules/*/process_roles.yaml (с учётом DISABLED_MODULES)."""
    root = Path(project_root).resolve()
    catalog = ModuleCatalog.from_env(root)
    rules: list[ProcessRoleRule] = []
    seen_ids: set[str] = set()

    for module_dir in catalog.iter_module_dirs():
        path = module_dir / PROCESS_ROLES_FILENAME
        if not path.is_file():
            continue
        for rule in _load_file(path, module_dir_name=module_dir.name):
            if rule.role_id in seen_ids:
                _warn(
                    t('process_role_duplicate', path=path, role_id=rule.role_id)
                )
                continue
            seen_ids.add(rule.role_id)
            rules.append(rule)

    return tuple(rules)


def collect_module_process_names(project_root: Path | str) -> frozenset[str]:
    rules = load_module_process_roles(str(Path(project_root).resolve()))
    names: set[str] = set()
    for rule in rules:
        names.update(rule.process_names)
    return frozenset(names)


def match_module_process_role(
    *,
    cmdline_text: str,
    cwd_text: str | None,
    project_root_text: str,
    rules: tuple[ProcessRoleRule, ...],
) -> str | None:
    """Первое совпавшее правило → role id. OR между when, AND внутри when."""
    for rule in rules:
        for when in rule.when:
            if when.cmdline_contains_any:
                if not any(marker in cmdline_text for marker in when.cmdline_contains_any):
                    continue
            if when.cwd_contains_any:
                if not cwd_text or not any(marker in cwd_text for marker in when.cwd_contains_any):
                    continue
            if when.project_bound:
                in_cmdline = project_root_text in cmdline_text
                in_cwd = bool(cwd_text and project_root_text in cwd_text)
                if not in_cmdline and not in_cwd:
                    continue
            # Пустой when с одним project_bound уже отфильтрован при парсинге
            # как «не пустой»; если только project_bound — условие выше достаточно.
            if (
                when.cmdline_contains_any
                or when.cwd_contains_any
                or when.project_bound
            ):
                return rule.role_id
    return None
