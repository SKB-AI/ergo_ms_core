"""Проверка, что локальный LLM API не слушает не-loopback (OLLAMA_HOST)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from security.catalog import Control, SecurityCatalog
from security.report import Finding

_LOOPBACK = frozenset({'127.0.0.1', 'localhost', '::1'})
_OPEN_BIND = frozenset({'0.0.0.0', '::', '*', '[::]'})


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def listen_host_from_value(raw: str) -> str:
    value = (raw or '').strip()
    if not value:
        return ''
    if '://' not in value:
        if value.startswith('['):
            end = value.find(']')
            return value[1:end].lower() if end > 0 else value.lower()
        return value.split(':')[0].lower()
    parsed = urlparse(value)
    return (parsed.hostname or '').strip().lower()


def is_loopback_or_unset(raw: str) -> bool:
    host = listen_host_from_value(raw)
    if not host:
        return True
    return host in _LOOPBACK


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    del catalog
    values = context['values']
    level = context['level']
    requirement = str(control.requirement(level) or 'any')
    env_key = control.env_key or 'OLLAMA_HOST'
    raw = (values.get(env_key) or '').strip()

    if requirement == 'any':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='на open bind LLM API не ограничивается',
        )

    if is_loopback_or_unset(raw):
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'{env_key} не задан или loopback',
        )

    host = listen_host_from_value(raw)
    extra = ' (все интерфейсы)' if host in _OPEN_BIND else ''
    if requirement == 'recommended':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='warning',
            message=f'{env_key} слушает не loopback{extra} (уровень {level})',
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity=_sev(control),
        message=f'{env_key} слушает не loopback{extra} (уровень {level})',
    )
