"""Проверка пароля Redis (databases.yaml) и публикации порта в Docker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ergo_modes import effective_redis_enabled
from security.catalog import Control, SecurityCatalog
from security.report import Finding

_PUBLISH_DISABLED = frozenset({'none', 'off', 'false', '0', '-', 'disabled', ''})


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def _parse_simple_yaml_section(text: str, section: str) -> dict[str, str]:
    """Минимальный разбор секции databases.yaml без PyYAML."""
    lines = text.splitlines()
    in_databases = False
    in_section = False
    section_indent = -1
    result: dict[str, str] = {}
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        stripped = raw.strip()
        if stripped == 'databases:':
            in_databases = True
            in_section = False
            continue
        if not in_databases:
            continue
        if indent == 2 and stripped.endswith(':'):
            name = stripped[:-1].strip()
            in_section = name == section
            section_indent = indent
            continue
        if in_section and indent > section_indent and ':' in stripped:
            key, _, value = stripped.partition(':')
            value = value.strip().strip('"').strip("'")
            result[key.strip()] = value
        elif in_section and indent <= section_indent and stripped.endswith(':'):
            break
    return result


def load_redis_password(root: Path) -> str:
    path = root / 'databases.yaml'
    if not path.is_file():
        return ''
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return ''
    section = _parse_simple_yaml_section(text, 'redis')
    return (section.get('password') or '').strip()


def redis_publish_port_explicit(values: dict[str, str]) -> int | None:
    """Числовой DOCKER_REDIS_PUBLISH_PORT или None (не публикуется / disabled)."""
    raw = (values.get('DOCKER_REDIS_PUBLISH_PORT') or '').strip().lower()
    if raw in _PUBLISH_DISABLED:
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    if port <= 0 or port > 65535:
        return None
    return port


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    root = Path(context['root'])
    requirement = str(control.requirement(level) or 'optional')

    if not effective_redis_enabled(values):
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='Redis не используется (ERGO_BROKER≠redis)',
        )

    password = load_redis_password(root)
    has_password = bool(password)

    if requirement == 'optional':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='на open пароль Redis не требуется',
        )

    if not has_password:
        if requirement == 'recommended':
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='warning',
                message=(
                    'databases.yaml redis.password пуст '
                    f'(уровень {level} рекомендует пароль)'
                ),
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=(
                'databases.yaml redis.password пуст '
                f'(уровень {level} требует пароль)'
            ),
        )

    if requirement == 'required_no_publish':
        published = redis_publish_port_explicit(values)
        if published is not None:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=(
                    f'DOCKER_REDIS_PUBLISH_PORT={published} — на уровне {level} '
                    'порт Redis на хост публиковать нельзя'
                ),
            )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message='пароль Redis задан в databases.yaml',
    )
