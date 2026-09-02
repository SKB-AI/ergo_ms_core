"""Сравнение .env с .env.example (только stdlib)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Активная строка или закомментированная директива: # KEY=value
_ENV_DIRECTIVE_RE = re.compile(r'^(?:#\s*)?([A-Z][A-Z0-9_]*)=')


@dataclass(frozen=True)
class EnvCompareResult:
    label: str
    example_path: Path
    env_path: Path
    example_exists: bool
    env_exists: bool
    example_active: int = 0
    example_documented: int = 0
    env_active: int = 0
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def has_missing(self) -> bool:
        return bool(self.missing)

    @property
    def has_extra(self) -> bool:
        return bool(self.extra)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_diff(self) -> bool:
        return self.has_missing or self.has_extra or self.has_errors


def _env_key_from_line(stripped: str) -> str | None:
    match = _ENV_DIRECTIVE_RE.match(stripped)
    if not match:
        return None
    return match.group(1)


def _env_assignment_from_line(stripped: str) -> tuple[str, str] | None:
    """KEY и строка KEY=VALUE без ведущего # (для подсказки при --show-example-values)."""
    match = _ENV_DIRECTIVE_RE.match(stripped)
    if not match:
        return None
    key = match.group(1)
    body = stripped[1:].lstrip() if stripped.startswith('#') else stripped
    return key, body


def parse_env_keys(path: Path) -> set[str]:
    """Возвращает ключи активных (незакомментированных) строк KEY=VALUE."""
    if not path.is_file():
        return set()

    keys: set[str] = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        key = _env_key_from_line(stripped)
        if key:
            keys.add(key)
    return keys


def parse_env_example_keys(path: Path) -> set[str]:
    """Ключи из .env.example: активные и закомментированные директивы (# KEY=value)."""
    if not path.is_file():
        return set()

    keys: set[str] = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key = _env_key_from_line(stripped)
        if key:
            keys.add(key)
    return keys


def parse_env_example_lines(path: Path) -> dict[str, str]:
    """Возвращает {KEY: строка KEY=VALUE} для активных и закомментированных директив."""
    if not path.is_file():
        return {}

    lines: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parsed = _env_assignment_from_line(stripped)
        if parsed:
            key, assignment = parsed
            lines[key] = assignment
    return lines


def compare_env_files(
    *,
    label: str,
    example_path: Path,
    env_path: Path,
) -> EnvCompareResult:
    example_exists = example_path.is_file()
    env_exists = env_path.is_file()

    if not example_exists:
        return EnvCompareResult(
            label=label,
            example_path=example_path,
            env_path=env_path,
            example_exists=False,
            env_exists=env_exists,
            errors=('example_missing',),
        )

    if not env_exists:
        return EnvCompareResult(
            label=label,
            example_path=example_path,
            env_path=env_path,
            example_exists=True,
            env_exists=False,
            errors=('env_missing',),
        )

    required_keys = parse_env_keys(example_path)
    documented_keys = parse_env_example_keys(example_path)
    env_keys = parse_env_keys(env_path)
    missing = tuple(sorted(required_keys - env_keys))
    extra = tuple(sorted(env_keys - documented_keys))

    return EnvCompareResult(
        label=label,
        example_path=example_path,
        env_path=env_path,
        example_exists=True,
        env_exists=True,
        example_active=len(required_keys),
        example_documented=len(documented_keys),
        env_active=len(env_keys),
        missing=missing,
        extra=extra,
    )
