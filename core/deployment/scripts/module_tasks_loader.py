"""
Загрузка modules/*/vscode.tasks.yaml: агрегация include_in для setup-full / start-all.

Discovery через ModuleCatalog. Вне Django — только YAML.
CLI: python module_tasks_loader.py --root PATH --json [--target setup-full|…]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from lifecycle.modules.catalog import ModuleCatalog  # noqa: E402

MODULE_TASKS_FILENAME = 'vscode.tasks.yaml'
INCLUDE_SETUP_FULL = 'setup-full'
INCLUDE_SETUP_FULL_AFTER_MIGRATE = 'setup-full-after-migrate'
INCLUDE_START_ALL = 'start-all'
INCLUDE_LOGS_ALL = 'logs-all'
VALID_INCLUDE_TARGETS = frozenset({
    INCLUDE_SETUP_FULL,
    INCLUDE_SETUP_FULL_AFTER_MIGRATE,
    INCLUDE_START_ALL,
    INCLUDE_LOGS_ALL,
})
_ERGOMS_COMMAND_RE = re.compile(r'^ergoms(\s|$)')


@dataclass(frozen=True)
class ModuleTaskEntry:
    module: str
    label: str
    detail: str
    command: str
    include_in: tuple[str, ...]
    order: int = 0
    hide: bool = False
    panel: str = 'shared'
    stop_command: str = ''
    service_key: str = ''
    optional: bool = False


@dataclass
class ModuleTasksAggregate:
    setup_full: list[ModuleTaskEntry] = field(default_factory=list)
    setup_full_after_migrate: list[ModuleTaskEntry] = field(default_factory=list)
    start_all: list[ModuleTaskEntry] = field(default_factory=list)
    logs_all: list[ModuleTaskEntry] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)


def _warn(message: str) -> None:
    print(format_console('warning', message), file=sys.stderr)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                items.append(text)
        return items
    return []


def _parse_order(value: Any) -> int:
    if value is None or value == '':
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _yaml_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or '').strip().lower()
    return normalized in {'1', 'true', 'yes'}


def _service_key_from_label(label: str, module: str) -> str:
    """Стабильный ключ для runtime YAML (без пробелов и спецсимволов)."""
    raw = label.strip() or module
    key = re.sub(r'[^a-zA-Z0-9]+', '_', raw).strip('_').lower()
    return key or module.replace('-', '_')


def _parse_tasks(path: Path, *, module_dir_name: str) -> list[ModuleTaskEntry]:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        _warn(t('yaml_read_failed', path=path, exc=exc))
        return []

    if not text.strip():
        return []

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _warn(t('yaml_parse_error', path=path, exc=exc))
        return []

    if not isinstance(data, dict):
        _warn(t('yaml_root_must_be_object', path=path))
        return []

    declared = str(data.get('module') or '').strip()
    if declared and declared != module_dir_name:
        _warn(
            t(
                'module_tasks_module_mismatch',
                module=module_dir_name,
                declared=declared,
            )
        )
        return []

    tasks_raw = data.get('tasks')
    if not isinstance(tasks_raw, list):
        return []

    entries: list[ModuleTaskEntry] = []
    for raw in tasks_raw:
        if not isinstance(raw, dict):
            _warn(t('module_tasks_task_not_object', path=path))
            continue
        label = str(raw.get('label') or '').strip()
        detail = str(raw.get('detail') or '').strip()
        command = str(raw.get('command') or '').strip()
        if not label or not detail or not command:
            _warn(t('module_tasks_missing_fields', path=path))
            continue
        if not _ERGOMS_COMMAND_RE.match(command):
            _warn(t('module_tasks_command_must_ergoms', path=path, label=label))
            continue

        include_raw = _as_str_list(raw.get('include_in'))
        include_in = tuple(t for t in include_raw if t in VALID_INCLUDE_TARGETS)
        for unknown in include_raw:
            if unknown not in VALID_INCLUDE_TARGETS:
                _warn(
                    t(
                        'module_tasks_unknown_include_in',
                        path=path,
                        label=label,
                        unknown=unknown,
                        allowed=', '.join(sorted(VALID_INCLUDE_TARGETS)),
                    )
                )

        panel_raw = str(raw.get('panel') or 'shared').strip().lower()
        panel = 'new' if panel_raw == 'new' else 'shared'
        stop_command = str(raw.get('stop_command') or '').strip()
        if stop_command and not _ERGOMS_COMMAND_RE.match(stop_command):
            _warn(t('module_tasks_stop_command_ignored', path=path, label=label))
            stop_command = ''

        entries.append(
            ModuleTaskEntry(
                module=module_dir_name,
                label=label,
                detail=detail,
                command=command,
                include_in=include_in,
                order=_parse_order(raw.get('order')),
                hide=_yaml_truthy(raw.get('hide')),
                panel=panel,
                stop_command=stop_command,
                service_key=_service_key_from_label(label, module_dir_name),
                optional=_yaml_truthy(raw.get('optional')),
            )
        )
    return entries


@lru_cache(maxsize=8)
def load_module_task_entries(project_root: str) -> tuple[ModuleTaskEntry, ...]:
    root = Path(project_root).resolve()
    catalog = ModuleCatalog.from_env(root)
    entries: list[ModuleTaskEntry] = []

    for module_dir in catalog.iter_module_dirs():
        path = module_dir / MODULE_TASKS_FILENAME
        if not path.is_file():
            continue
        entries.extend(_parse_tasks(path, module_dir_name=module_dir.name))

    return tuple(entries)


def _sort_key(entry: ModuleTaskEntry) -> tuple[int, str, str]:
    return (entry.order, entry.module, entry.label)


def aggregate_module_tasks(project_root: Path | str) -> ModuleTasksAggregate:
    entries = load_module_task_entries(str(Path(project_root).resolve()))
    agg = ModuleTasksAggregate()
    modules_seen: set[str] = set()

    for entry in entries:
        if not entry.include_in:
            continue
        if entry.module not in modules_seen:
            modules_seen.add(entry.module)
            agg.modules.append(entry.module)
        if INCLUDE_SETUP_FULL in entry.include_in:
            agg.setup_full.append(entry)
        if INCLUDE_SETUP_FULL_AFTER_MIGRATE in entry.include_in:
            agg.setup_full_after_migrate.append(entry)
        if INCLUDE_START_ALL in entry.include_in:
            agg.start_all.append(entry)
        if INCLUDE_LOGS_ALL in entry.include_in:
            agg.logs_all.append(entry)

    agg.modules.sort()
    agg.setup_full.sort(key=_sort_key)
    agg.setup_full_after_migrate.sort(key=_sort_key)
    agg.start_all.sort(key=_sort_key)
    agg.logs_all.sort(key=_sort_key)
    return agg


def tasks_for_target(project_root: Path | str, target: str) -> list[ModuleTaskEntry]:
    agg = aggregate_module_tasks(project_root)
    if target == INCLUDE_SETUP_FULL:
        return list(agg.setup_full)
    if target == INCLUDE_SETUP_FULL_AFTER_MIGRATE:
        return list(agg.setup_full_after_migrate)
    if target == INCLUDE_START_ALL:
        return list(agg.start_all)
    if target == INCLUDE_LOGS_ALL:
        return list(agg.logs_all)
    return []


def dump_module_tasks_json(project_root: Path | str, *, target: str | None = None) -> str:
    if target:
        items = tasks_for_target(project_root, target)
        payload = {
            'target': target,
            'tasks': [asdict(t) for t in items],
        }
    else:
        agg = aggregate_module_tasks(project_root)
        payload = {
            'modules': agg.modules,
            'setup_full': [asdict(t) for t in agg.setup_full],
            'setup_full_after_migrate': [asdict(t) for t in agg.setup_full_after_migrate],
            'start_all': [asdict(t) for t in agg.start_all],
            'logs_all': [asdict(t) for t in agg.logs_all],
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description=t('module_tasks_description'))
    parser.add_argument('--root', type=Path, default=None, help=t('help_root_path'))
    parser.add_argument('--json', action='store_true', help=t('help_json_stdout'))
    parser.add_argument(
        '--target',
        choices=sorted(VALID_INCLUDE_TARGETS),
        default=None,
        help='Только задачи с данным include_in',
    )
    args = parser.parse_args(argv)

    root = args.root
    if root is None:
        root = _DEPLOYMENT_DIR.parent.parent
    root = root.resolve()
    if not (root / 'pyproject.toml').is_file():
        print(format_console('error', t('project_root_not_found', root=root)), file=sys.stderr)
        return 1

    if args.json:
        print(dump_module_tasks_json(root, target=args.target))
        return 0

    agg = aggregate_module_tasks(root)
    print(t('modules_list', items=', '.join(agg.modules) or t('modules_none')))
    print(t('setup_full_label', count=len(agg.setup_full)))
    for entry in agg.setup_full:
        print(t('module_task_entry', module=entry.module, label=entry.label, command=entry.command))
    print(t('setup_full_after_migrate_label', count=len(agg.setup_full_after_migrate)))
    for entry in agg.setup_full_after_migrate:
        print(t('module_task_entry', module=entry.module, label=entry.label, command=entry.command))
    print(t('start_all_label', count=len(agg.start_all)))
    for entry in agg.start_all:
        print(t('module_task_entry', module=entry.module, label=entry.label, command=entry.command))
    print(t('logs_all_label', count=len(agg.logs_all)))
    for entry in agg.logs_all:
        print(t('module_task_entry', module=entry.module, label=entry.label, command=entry.command))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
