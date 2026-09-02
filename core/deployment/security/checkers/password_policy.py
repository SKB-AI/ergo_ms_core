"""Проверка политики паролей по env (без Django validators)."""

from __future__ import annotations

from typing import Any

from security.catalog import Control, SecurityCatalog
from security.compare import env_truthy, parse_int
from security.levels import security_level_rank
from security.report import Finding


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    min_required = parse_int(control.requirement(level))
    if min_required is None:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'на уровне {level} минимум не задан',
        )

    raw = (values.get(control.env_key or 'API_PASSWORD_MIN_LENGTH') or '').strip()
    # Дефолт кода: 8 (settings/password.py)
    actual = parse_int(raw) if raw else 8
    if actual is None:
        return Finding(
            control.id,
            'skip',
            f'нецелое значение {control.env_key}',
            title=control.title,
        )

    problems: list[str] = []
    if actual < min_required:
        problems.append(f'min_length={actual} < {min_required}')

    if security_level_rank(level) >= 1:
        digit = env_truthy(values.get('API_PASSWORD_REQUIRE_DIGIT'))
        lower = env_truthy(values.get('API_PASSWORD_REQUIRE_LOWERCASE'))
        # unset → дефолт кода True
        if digit is False:
            problems.append('API_PASSWORD_REQUIRE_DIGIT=false')
        if lower is False:
            problems.append('API_PASSWORD_REQUIRE_LOWERCASE=false')

    if problems:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=', '.join(problems),
        )
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'min_length={actual} >= {min_required}',
    )
