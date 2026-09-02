"""Проверка API_CSP_MODE vs профиль (С11 phase 1)."""

from __future__ import annotations

from typing import Any

from security.catalog import Control, SecurityCatalog
from security.csp_policy import (
    CSP_ENV_KEY,
    CSP_MODE_RANK,
    DEFAULT_CSP_MODE,
    normalize_csp_mode,
)
from security.report import Finding


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    required = normalize_csp_mode(str(control.requirement(level) or DEFAULT_CSP_MODE))
    env_key = control.env_key or CSP_ENV_KEY
    raw = values.get(env_key)
    if raw is None or str(raw).strip() == '':
        actual = required
        source = 'профиль (ключ не задан)'
    else:
        actual = normalize_csp_mode(str(raw))
        source = env_key

    req_rank = CSP_MODE_RANK[required]
    act_rank = CSP_MODE_RANK[actual]

    if act_rank < req_rank:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано {actual} ({source}), уровень {level} требует не слабее {required}',
        )

    # maximum: phase 1 stub — режим есть, полный аудит внешних доменов ещё нет.
    if required == 'no_unsafe_plus_externals':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='warning',
            message=(
                f'phase 1: режим {actual} ({source}); уровень {level} требует {required}; '
                'полный список внешних источников ещё не зафиксирован'
            ),
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{actual} соответствует минимуму {required} ({source})',
    )
