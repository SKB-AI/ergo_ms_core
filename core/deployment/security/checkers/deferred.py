"""Отложенные и code_fixed проверки."""

from __future__ import annotations

from typing import Any

from security.catalog import Control, SecurityCatalog
from security.report import Finding
from security.levels import security_level_rank


def deferred(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='skip',
        message='проверка кода/сканер будет позже (этап 2+)',
    )


def code_fixed(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    """
    Контроль закрыт правкой ядра; env-проверка недоступна.
    Для уровней, где требование «выключено/optional», не шумим.
    """
    level = context['level']
    requirement = control.requirement(level)
    rank = security_level_rank(level)

    # На open многие code_fixed контроли не требуются
    if requirement in (False, 'false', 'optional', 'none', 'allow', 'recommended', None):
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'на уровне {level} не требуется / опционально',
        )

    if control.status == 'planned':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='skip',
            message='в каталоге status=planned',
        )

    # standard+ с implemented/partial — считаем OK с пояснением
    if control.status in {'implemented', 'partial'} and rank >= 1:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='зафиксировано в коде ядра (env-проверка недоступна)',
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='skip',
        message='runtime в коде; автоматическая проверка не реализована',
    )
