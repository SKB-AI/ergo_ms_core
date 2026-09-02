"""Проверка шаблонных секретов без печати значений."""

from __future__ import annotations

from typing import Any

from security.catalog import Control, SecurityCatalog
from security.levels import security_level_rank
from security.report import Finding

_MIN_LENGTH_HARDENED = 32


def secrets_no_defaults(
    control: Control,
    catalog: SecurityCatalog,
    context: dict[str, Any],
) -> Finding:
    values: dict[str, str] = context['values']
    level = context['level']
    rank = security_level_rank(level)
    requirement = str(control.requirement(level) or '')

    if requirement == 'allow_template' or rank <= 0:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='на уровне open шаблонный секрет допускается',
        )

    secret = (values.get(control.env_key or 'API_SECRET_KEY') or '').strip()
    insecure = catalog.insecure_secret_values()

    if not secret:
        sev = 'error' if control.violation == 'error' else 'warning'
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=sev,
            message='API_SECRET_KEY пуст',
        )

    if secret in insecure:
        sev = 'error' if control.violation == 'error' else 'warning'
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=sev,
            message='API_SECRET_KEY совпадает со значением из шаблона',
        )

    if rank >= 2 and len(secret) < _MIN_LENGTH_HARDENED:
        sev = 'error' if control.violation == 'error' else 'warning'
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=sev,
            message=f'API_SECRET_KEY короче {_MIN_LENGTH_HARDENED} символов',
        )

    if rank >= 3:
        jwt_key = (values.get('API_JWT_SIGNING_KEY') or '').strip()
        if not jwt_key:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='warning',
                message='на maximum рекомендуется отдельный API_JWT_SIGNING_KEY',
            )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message='API_SECRET_KEY не совпадает с шаблонными значениями',
    )
