"""Разбор commands.conf: составные шаги и платформенные ветки.

Только стандартная библиотека — модуль читают unit-тесты и не тянут PyYAML.
"""

from __future__ import annotations

import re
from pathlib import Path

_GENERIC_PREFIXES = frozenset({
    'lifecycle',
    'api',
    'npm',
    'poetry',
    'shell',
    'media_api',
})

_PLATFORM_PREFIXES = frozenset({'win', 'linux'})

_CONF_LINE = re.compile(r'^([a-zA-Z0-9_-]+)=(.+)$')

# Службы и обёртки, которых нет в commands.conf (ergo_ms.ps1 / ergo_ms.sh).
SHARED_WRAPPER_COMMANDS = frozenset({
    'help',
    'install',
    'install-services',
    'install-api-service',
    'install-client-service',
    'install-worker-service',
    'install-beat-service',
    'install-media-service',
    'start',
    'stop',
    'restart',
    'status',
    'uninstall-services',
    'install-cli',
    'uninstall-cli',
    'logs',
    'setup-full',
    'clean',
    'update-submodules',
    'update-module-submodules',
    'install-nginx',
    'install-nginx-service',
    'uninstall-nginx',
    'start-nginx',
    'stop-nginx',
    'restart-nginx',
    'reload-nginx',
    'status-nginx',
    'test-nginx',
    'install-redis',
    'install-redis-service',
    'uninstall-redis',
    'start-redis',
    'stop-redis',
    'restart-redis',
    'status-redis',
    'test-redis',
    'install-postgres',
    'install-postgres-service',
    'uninstall-postgres',
    'start-postgres',
    'stop-postgres',
    'restart-postgres',
    'status-postgres',
    'test-postgres',
    'migrate-postgres-to-portable',
    'install-python',
    'install-python-runtime',
    'install-nodejs',
    'install-node',
})

LINUX_ONLY_WRAPPER_COMMANDS = frozenset({
    'install-tls',
    'renew-tls',
    'status-tls',
})

PROXY_WRAPPER_COMMANDS = frozenset({'poetry', 'api', 'media_api', 'npm'})


def split_composite_command(command_def: str) -> list[str]:
    """Режет значение commands.conf по ``&&`` вне кавычек."""
    parts: list[str] = []
    buf: list[str] = []
    quote = ''
    escaped = False
    index = 0
    text = command_def or ''
    while index < len(text):
        char = text[index]
        if quote:
            buf.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = ''
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            buf.append(char)
            index += 1
            continue
        if text.startswith('&&', index):
            part = ''.join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            index += 2
            continue
        buf.append(char)
        index += 1
    tail = ''.join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_commands_conf(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = _CONF_LINE.match(line)
        if not match:
            continue
        result[match.group(1)] = match.group(2).strip()
    return result


def load_commands_conf(path: Path) -> dict[str, str]:
    return parse_commands_conf(path.read_text(encoding='utf-8'))


def _step_prefix(step: str) -> str | None:
    if ':' not in step:
        return None
    prefix = step.split(':', 1)[0].strip()
    if prefix in _GENERIC_PREFIXES or prefix in _PLATFORM_PREFIXES:
        return prefix
    return None


def platform_prefixes(command_def: str) -> set[str]:
    found: set[str] = set()
    for step in split_composite_command(command_def):
        prefix = _step_prefix(step)
        if prefix in _PLATFORM_PREFIXES:
            found.add(prefix)
    return found


def needs_both_platform_arms(command_def: str) -> bool:
    return bool(platform_prefixes(command_def))


def missing_platform_arms(command_def: str) -> set[str]:
    found = platform_prefixes(command_def)
    if not found:
        return set()
    return _PLATFORM_PREFIXES - found


def extract_linux_case_commands(script_text: str) -> set[str]:
    match = re.search(
        r'case\s+"\$command"\s+in\s+([^\n]+)',
        script_text,
    )
    if not match:
        return set()
    names: set[str] = set()
    for raw in match.group(1).split('|'):
        name = raw.strip().rstrip(')')
        if name and '*' not in name and not name.startswith(':'):
            names.add(name)
    return names


def _extract_powershell_string_array(script_text: str, assignment: str) -> set[str]:
    pattern = re.compile(
        rf'{re.escape(assignment)}\s*=\s*@\((.*?)\)',
        re.S,
    )
    match = pattern.search(script_text)
    if not match:
        return set()
    return set(re.findall(r"'([a-zA-Z0-9_-]+)'", match.group(1)))


def extract_windows_wrapper_commands(script_text: str) -> set[str]:
    names = set()
    names.update(_extract_powershell_string_array(script_text, '$adminCommands'))
    names.update(_extract_powershell_string_array(script_text, '$noAdminCommands'))
    names.update(_extract_powershell_string_array(script_text, '$proxyCommands'))
    return names
