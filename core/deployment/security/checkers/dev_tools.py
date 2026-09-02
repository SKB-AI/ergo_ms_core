"""Проверка ERGO_DEV_TOOLS: в production overlay прав запрещён."""

from __future__ import annotations

from typing import Any

from ergo_modes import effective_deploy_type
from security.catalog import Control, SecurityCatalog
from security.compare import env_truthy
from security.report import Finding


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    enabled = bool(env_truthy(values.get('ERGO_DEV_TOOLS')))
    deploy = effective_deploy_type(values)
    env_key = control.env_key or 'ERGO_DEV_TOOLS'

    if enabled and deploy == 'production':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='error',
            message=f'{env_key} включён при ERGO_ENV=production; overlay прав недопустим',
        )
    if enabled:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'{env_key} включён (только development)',
        )
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{env_key} выключен',
    )
