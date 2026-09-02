"""Проверка API_ADP_DEFAULT_VIEW_GRANTS vs профиль (С7)."""

from __future__ import annotations

from typing import Any

from security.catalog import Control, SecurityCatalog
from security.report import Finding

# Слабее профиля: granted < denied (denied строже).
_MODE_RANK = {
    'granted': 0,
    'denied': 1,
}

_DEFAULT_MODE = 'granted'


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def _normalize_mode(raw: str | None) -> str:
    text = (raw or '').strip().lower()
    if text in _MODE_RANK:
        return text
    return _DEFAULT_MODE


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    required = _normalize_mode(str(control.requirement(level) or _DEFAULT_MODE))
    env_key = control.env_key or 'API_ADP_DEFAULT_VIEW_GRANTS'
    raw = values.get(env_key)
    if raw is None or str(raw).strip() == '':
        actual = required
        source = 'профиль (ключ не задан)'
    else:
        actual = _normalize_mode(str(raw))
        source = env_key

    if _MODE_RANK[actual] < _MODE_RANK[required]:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано {actual} ({source}), уровень {level} требует не слабее {required}',
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{actual} соответствует минимуму {required} ({source})',
    )
