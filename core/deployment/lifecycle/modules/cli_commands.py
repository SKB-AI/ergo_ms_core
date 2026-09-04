"""
Роль процесса для Django-команд модуля.

В MODULE_RUNTIME=microservice ядро не кладёт MICROSERVICE_MODULES в INSTALLED_APPS.
Команда из modules/<name>/api/management/commands/ тогда «неизвестна», пока
процесс не запущен как module:<name>.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

_API_IN_DEF = re.compile(r'(?:^|&&\s*)api:')


def module_name_from_cli_command(cmd_name: str) -> str | None:
    """``<name>:sync-knowledge`` → ``<name>``; иначе None."""
    raw = (cmd_name or '').strip()
    if ':' not in raw:
        return None
    name = raw.split(':', 1)[0].strip()
    return name or None


def module_process_role_for_cli_command(cmd_name: str, command_def: str) -> str | None:
    """Если команда модуля ведёт на ``api:``, вернуть ``module:<name>``."""
    name = module_name_from_cli_command(cmd_name)
    if not name:
        return None
    if not _API_IN_DEF.search(command_def or ''):
        return None
    return f'module:{name}'


def find_modules_owning_management_command(
    command_name: str,
    modules_dir: Path,
    *,
    disabled: Iterable[str] = (),
) -> list[str]:
    """Имена модулей с ``api/management/commands/<command_name>.py``."""
    name = (command_name or '').strip()
    if not name or name.startswith('_') or '/' in name or '\\' in name:
        return []
    if not name.isidentifier() or name.endswith('_lib'):
        return []

    root = Path(modules_dir)
    if not root.is_dir():
        return []

    skipped = frozenset(item.strip() for item in disabled if item and item.strip())
    owners: list[str] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []

    for entry in entries:
        if not entry.is_dir() or entry.name in skipped or entry.name.startswith('.'):
            continue
        cmd_path = entry / 'api' / 'management' / 'commands' / f'{name}.py'
        try:
            if cmd_path.is_file():
                owners.append(entry.name)
        except OSError:
            continue
    return sorted(owners)


def bind_cli_module_process_role(role: str) -> None:
    """Ставит ``ERGO_PROCESS_ROLE``, если ещё пусто."""
    if (os.environ.get('ERGO_PROCESS_ROLE') or '').strip():
        return
    value = (role or '').strip()
    if value:
        os.environ['ERGO_PROCESS_ROLE'] = value
