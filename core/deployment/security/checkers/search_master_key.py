"""Проверка MEILI_MASTER_KEY: не оставлять шаблонный ключ поиска."""

from __future__ import annotations

from typing import Any

from ergo_modes import effective_search_enabled
from security.catalog import Control, SecurityCatalog
from security.report import Finding


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    requirement = str(control.requirement(level) or 'allow_template')

    if not effective_search_enabled(values):
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='поиск выключен (ERGO_SEARCH_ENABLED)',
        )

    if requirement == 'allow_template':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='на open шаблонный ключ поиска допускается',
        )

    env_key = control.env_key or 'MEILI_MASTER_KEY'
    secret = (values.get(env_key) or '').strip()
    insecure = catalog.ref_strings('insecure_search_keys')
    lowered = {item.lower() for item in insecure}
    is_template = (not secret) or secret.lower() in lowered

    if not is_template:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='ключ поиска не совпадает с шаблоном',
        )

    if requirement == 'recommended':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='warning',
            message=f'{env_key} пуст или из шаблона (уровень {level} рекомендует свой ключ)',
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity=_sev(control),
        message=f'{env_key} пуст или из шаблона (уровень {level} требует свой ключ)',
    )
