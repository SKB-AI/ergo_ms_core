"""Проверка режима доступа Jupyter (семантика validate_jupyter_startup / ergo_modes)."""

from __future__ import annotations

from typing import Any, Mapping

from ergo_modes import (
    effective_jupyter_access_mode,
    effective_jupyter_behind_nginx,
    effective_jupyter_enabled,
    ergo_jupyter,
)
from security.catalog import Control, SecurityCatalog
from security.compare import env_truthy
from security.report import Finding


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def _truthy(values: Mapping[str, str], key: str) -> bool:
    return env_truthy(values.get(key)) is True


def _resolve_access_mode(values: Mapping[str, str]) -> str:
    explicit = (values.get('API_JUPYTER_ACCESS_MODE') or '').strip().lower()
    if explicit in {'local', 'lan', 'nginx'}:
        return explicit

    from_ergo = effective_jupyter_access_mode(values)
    if from_ergo in {'local', 'lan', 'nginx'}:
        return from_ergo

    # auto / none — эвристика как в jupyter_runtime.effective_jupyter_access_mode
    if effective_jupyter_behind_nginx(values):
        return 'nginx'
    if _truthy(values, 'API_JUPYTER_ALLOW_REMOTE'):
        return 'lan'
    return 'local'


def _has_token(values: Mapping[str, str]) -> bool:
    return bool((values.get('API_JUPYTER_TOKEN') or '').strip())


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    requirement = str(control.requirement(level) or 'any')

    if requirement == 'any':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='на open ограничения Jupyter не требуются',
        )

    enabled = effective_jupyter_enabled(values)
    jupyter_mode = ergo_jupyter(values)

    if requirement == 'none':
        if enabled:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=f'ERGO_JUPYTER={jupyter_mode}, уровень {level} требует none',
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='Jupyter выключен',
        )

    if not enabled:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='Jupyter выключен',
        )

    access = _resolve_access_mode(values)
    has_token = _has_token(values)

    if requirement == 'not_open_lan_without_auth':
        if access in {'lan', 'nginx'} and not has_token:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=(
                    f'режим доступа {access} без API_JUPYTER_TOKEN '
                    f'(уровень {level})'
                ),
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'доступ {access}, токен {"задан" if has_token else "не требуется"}',
        )

    if requirement == 'local_or_proxied_auth':
        if access == 'lan':
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=f'режим lan недопустим на уровне {level} (только local или nginx)',
            )
        if access == 'nginx' and not has_token:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message='режим nginx без API_JUPYTER_TOKEN',
            )
        if access == 'nginx' and not effective_jupyter_behind_nginx(values):
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message='режим nginx требует ERGO_PROXY=nginx (или NGINX_ENABLED=true)',
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'доступ {access}',
        )

    return Finding(
        control.id,
        'skip',
        f'неизвестное требование {requirement}',
        title=control.title,
    )
