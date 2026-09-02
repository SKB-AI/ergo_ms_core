"""
Загрузка modules/*/host_lifecycle.yaml для install/uninstall-services и тестов.

Discovery через ModuleCatalog. Вне Django — только YAML.
CLI: python host_lifecycle_loader.py --root PATH --json
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
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

HOST_LIFECYCLE_FILENAME = 'host_lifecycle.yaml'
_INSTALL_MODULE_SERVICE = 'install-module-service'
_UNINSTALL_MODULE_SERVICE = 'uninstall-module-service'


@dataclass(frozen=True)
class HostLifecycleEntry:
    module: str
    stop_commands: tuple[str, ...] = ()
    install_service_commands: tuple[str, ...] = ()
    uninstall_service_commands: tuple[str, ...] = ()
    service_units: tuple[str, ...] = ()


@dataclass
class HostLifecycleAggregate:
    stop_commands: list[str] = field(default_factory=list)
    install_service_commands: list[str] = field(default_factory=list)
    uninstall_service_commands: list[str] = field(default_factory=list)
    service_units: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    skipped_process_install_commands: list[str] = field(default_factory=list)


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


def _safe_module_token(name: str) -> str:
    return ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in name)


def parse_module_process_service_command(cmd: str) -> tuple[str, str, str] | None:
    """Разбор install/uninstall-module-service: (install|uninstall, module, kind)."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    if not parts:
        return None
    verb = parts[0]
    if verb == _INSTALL_MODULE_SERVICE:
        action = 'install'
    elif verb == _UNINSTALL_MODULE_SERVICE:
        action = 'uninstall'
    else:
        return None
    module = ''
    kind = 'api'
    index = 1
    while index < len(parts):
        token = parts[index]
        if token.startswith('--module='):
            module = token.split('=', 1)[1]
        elif token == '--module' and index + 1 < len(parts):
            index += 1
            module = parts[index]
        elif token.startswith('--kind='):
            kind = token.split('=', 1)[1]
        elif token == '--kind' and index + 1 < len(parts):
            index += 1
            kind = parts[index]
        index += 1
    module = module.strip()
    kind = kind.strip() or 'api'
    if not module:
        return None
    return action, module, kind


def process_service_unit_name(module: str, kind: str, prefix: str | None = None) -> str:
    from service_names import ServiceNames

    return ServiceNames(prefix).module(module, kind)


def is_module_process_service_unit(unit: str, module: str) -> bool:
    short = unit[: -len('.service')] if unit.endswith('.service') else unit
    return short in (
        process_service_unit_name(module, 'api'),
        process_service_unit_name(module, 'worker'),
        process_service_unit_name(module, 'beat'),
    )


def _parse_file(path: Path, *, module_dir_name: str) -> HostLifecycleEntry | None:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        _warn(t('yaml_read_failed', path=path, exc=exc))
        return None

    if not text.strip():
        _warn(t('yaml_file_empty', path=path))
        return None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _warn(t('yaml_parse_error', path=path, exc=exc))
        return None

    if not isinstance(data, dict):
        _warn(t('yaml_root_must_be_object', path=path))
        return None

    module = str(data.get('module') or module_dir_name).strip() or module_dir_name
    host = data.get('host')
    if host is None:
        # Допускаем плоский формат без обёртки host:
        host = {
            'stop_commands': data.get('stop_commands'),
            'install_service_commands': data.get('install_service_commands'),
            'uninstall_service_commands': data.get('uninstall_service_commands'),
            'service_units': data.get('service_units'),
        }
    if not isinstance(host, dict):
        _warn(t('host_section_must_be_object', path=path))
        return None

    stop = tuple(_as_str_list(host.get('stop_commands')))
    install = tuple(_as_str_list(host.get('install_service_commands')))
    uninstall = tuple(_as_str_list(host.get('uninstall_service_commands')))
    units = tuple(_as_str_list(host.get('service_units')))

    if not stop and not install and not uninstall and not units:
        _warn(t('host_section_empty', path=path))
        return None

    if install and not uninstall:
        _warn(
            t(
                'host_lifecycle_install_without_uninstall',
                path=path,
                module=module,
            )
        )
    if uninstall and not install:
        _warn(
            t(
                'host_lifecycle_uninstall_without_install',
                path=path,
                module=module,
            )
        )

    return HostLifecycleEntry(
        module=module,
        stop_commands=stop,
        install_service_commands=install,
        uninstall_service_commands=uninstall,
        service_units=units,
    )


@lru_cache(maxsize=8)
def load_host_lifecycle_entries(project_root: str) -> tuple[HostLifecycleEntry, ...]:
    root = Path(project_root).resolve()
    catalog = ModuleCatalog.from_env(root)
    entries: list[HostLifecycleEntry] = []

    for module_dir in catalog.iter_module_dirs():
        path = module_dir / HOST_LIFECYCLE_FILENAME
        if not path.is_file():
            continue
        entry = _parse_file(path, module_dir_name=module_dir.name)
        if entry is not None:
            entries.append(entry)

    return tuple(entries)


def _kind_from_process_unit(unit: str, module: str) -> str | None:
    short = unit[: -len('.service')] if unit.endswith('.service') else unit
    if short == process_service_unit_name(module, 'api'):
        return 'api'
    if short == process_service_unit_name(module, 'worker'):
        return 'worker'
    if short == process_service_unit_name(module, 'beat'):
        return 'beat'
    return None


def allows_module_kind(catalog: ModuleCatalog, host_profile, module: str, kind: str) -> bool:
    if not catalog.allows_module_process_os_services(module):
        return False
    if kind == 'api':
        return host_profile.wants('module_api')
    if kind == 'worker':
        return host_profile.wants('module_worker')
    if kind == 'beat':
        return host_profile.wants('module_beat')
    return True


def aggregate_host_lifecycle(project_root: Path | str) -> HostLifecycleAggregate:
    root = Path(project_root).resolve()
    entries = load_host_lifecycle_entries(str(root))
    catalog = ModuleCatalog.from_project_env(root)
    from lifecycle.host_profile import resolve_host_profile_from_root  # noqa: WPS433

    host_profile = resolve_host_profile_from_root(root)
    agg = HostLifecycleAggregate()
    seen_stop: set[str] = set()
    seen_install: set[str] = set()
    seen_uninstall: set[str] = set()
    seen_units: set[str] = set()
    seen_skipped: set[str] = set()

    for entry in entries:
        agg.modules.append(entry.module)
        for cmd in entry.stop_commands:
            if cmd not in seen_stop:
                seen_stop.add(cmd)
                agg.stop_commands.append(cmd)
        for cmd in entry.install_service_commands:
            parsed = parse_module_process_service_command(cmd)
            if parsed is not None:
                _action, _module, kind = parsed
                if not allows_module_kind(catalog, host_profile, entry.module, kind):
                    if cmd not in seen_skipped:
                        seen_skipped.add(cmd)
                        agg.skipped_process_install_commands.append(cmd)
                    continue
            if cmd not in seen_install:
                seen_install.add(cmd)
                agg.install_service_commands.append(cmd)
        for cmd in entry.uninstall_service_commands:
            if cmd not in seen_uninstall:
                seen_uninstall.add(cmd)
                agg.uninstall_service_commands.append(cmd)
        for unit in entry.service_units:
            kind = _kind_from_process_unit(unit, entry.module)
            if kind is not None and not allows_module_kind(
                catalog, host_profile, entry.module, kind
            ):
                continue
            if unit not in seen_units:
                seen_units.add(unit)
                agg.service_units.append(unit)

    return agg


def unit_is_installed(name: str) -> bool:
    """Есть ли OS-служба: linked systemd unit или служба Windows."""
    unit = name if name.endswith('.service') else f'{name}.service'
    short = unit[: -len('.service')]
    if os.name == 'nt':
        result = subprocess.run(
            ['sc', 'query', short],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    path = Path('/etc/systemd/system') / unit
    if path.is_file() or path.is_symlink():
        return True
    result = subprocess.run(
        ['systemctl', 'list-unit-files', '--type=service', '--no-legend', unit],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool((result.stdout or '').strip())


def collect_uninstall_service_commands(project_root: Path | str) -> list[str]:
    """uninstall_service_commands только для модулей, у которых unit уже стоит.

    Если в записи нет service_units — команды всё равно выполняем (скрипт сам
    разбирается, что удалять).
    """
    seen: set[str] = set()
    commands: list[str] = []
    for entry in load_host_lifecycle_entries(str(Path(project_root).resolve())):
        if not entry.uninstall_service_commands:
            continue
        if entry.service_units and not any(unit_is_installed(unit) for unit in entry.service_units):
            continue
        for cmd in entry.uninstall_service_commands:
            if cmd in seen:
                continue
            seen.add(cmd)
            commands.append(cmd)
    return commands


def collect_stale_module_process_uninstall_commands(project_root: Path | str) -> list[str]:
    """Снять API/worker OS-службы модуля, если текущий режим их больше не ставит."""
    root = Path(project_root).resolve()
    catalog = ModuleCatalog.from_project_env(root)
    from lifecycle.host_profile import resolve_host_profile_from_root  # noqa: WPS433

    host_profile = resolve_host_profile_from_root(root)
    seen: set[str] = set()
    commands: list[str] = []
    for entry in load_host_lifecycle_entries(str(root)):
        for cmd in entry.uninstall_service_commands:
            parsed = parse_module_process_service_command(cmd)
            if parsed is None or parsed[0] != 'uninstall':
                continue
            kind = parsed[2]
            if allows_module_kind(catalog, host_profile, entry.module, kind):
                continue
            unit = process_service_unit_name(entry.module, kind)
            if not unit_is_installed(unit):
                continue
            if cmd in seen:
                continue
            seen.add(cmd)
            commands.append(cmd)
    return commands


def dump_host_lifecycle_json(project_root: Path | str) -> str:
    agg = aggregate_host_lifecycle(project_root)
    return json.dumps(asdict(agg), ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description=t('host_lifecycle_description'))
    parser.add_argument('--root', type=Path, default=None, help=t('help_root_path'))
    parser.add_argument('--json', action='store_true', help=t('help_json_stdout'))
    parser.add_argument(
        '--units',
        action='store_true',
        help='Вывести service_units по одному на строку (для start/stop)',
    )
    parser.add_argument(
        '--stop-commands',
        action='store_true',
        help='Вывести stop_commands по одному на строку',
    )
    parser.add_argument(
        '--stop-commands-paired',
        action='store_true',
        help='stop_commands с service_units: cmd<TAB>unit1 unit2 (для ergoms stop)',
    )
    args = parser.parse_args(argv)

    root = args.root
    if root is None:
        # scripts → deployment → core → project
        root = _DEPLOYMENT_DIR.parent.parent
    root = root.resolve()
    if not (root / 'pyproject.toml').is_file():
        print(format_console('error', t('project_root_not_found', root=root)), file=sys.stderr)
        return 1

    if args.json:
        print(dump_host_lifecycle_json(root))
        return 0

    if args.stop_commands_paired:
        for entry in load_host_lifecycle_entries(str(root)):
            units = ' '.join(entry.service_units)
            for cmd in entry.stop_commands:
                print(f'{cmd}\t{units}')
        return 0

    agg = aggregate_host_lifecycle(root)
    if args.units:
        for unit in agg.service_units:
            print(unit)
        return 0
    if args.stop_commands:
        for cmd in agg.stop_commands:
            print(cmd)
        return 0

    print(t('modules_list', items=', '.join(agg.modules) or t('modules_none')))
    print(f'stop_commands: {len(agg.stop_commands)}')
    for cmd in agg.stop_commands:
        print(f'  - {cmd}')
    print(f'install_service_commands: {len(agg.install_service_commands)}')
    for cmd in agg.install_service_commands:
        print(f'  - {cmd}')
    print(f'uninstall_service_commands: {len(agg.uninstall_service_commands)}')
    for cmd in agg.uninstall_service_commands:
        print(f'  - {cmd}')
    print(f'service_units: {len(agg.service_units)}')
    for unit in agg.service_units:
        print(f'  - {unit}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
