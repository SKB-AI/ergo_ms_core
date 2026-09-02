"""Проверка CLIENT_BROWSER_LOG_ENABLED относительно профиля."""

from __future__ import annotations

from typing import Any

from security.catalog import Control, SecurityCatalog
from security.compare import env_truthy
from security.report import Finding


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def _effective_enabled(values: dict[str, str]) -> bool:
    flag = env_truthy(values.get('CLIENT_BROWSER_LOG_ENABLED'))
    # Дефолт кода: True
    return True if flag is None else flag


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    requirement = control.requirement(level)
    enabled = _effective_enabled(values)
    env_key = control.env_key or 'CLIENT_BROWSER_LOG_ENABLED'

    if requirement in (True, 'true'):
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'{env_key}={"true" if enabled else "false"} допустим на {level}',
        )

    if requirement == 'true_no_pii':
        # Санитизация PII — в ClientBrowserLogView; здесь только факт включения
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='включён; санитизация чувствительных полей в коде ядра',
        )

    if requirement in (False, 'false'):
        if enabled:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=f'{env_key} включён, уровень {level} требует выключения',
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'{env_key} выключен',
        )

    return Finding(
        control.id,
        'skip',
        f'неизвестное требование {requirement!r}',
        title=control.title,
    )
