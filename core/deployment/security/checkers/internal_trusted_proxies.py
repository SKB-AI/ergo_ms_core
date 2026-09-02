"""Проверка trusted proxies для internal media API (С3)."""

from __future__ import annotations

import hmac
from typing import Any

from security.catalog import Control, SecurityCatalog
from security.report import Finding

_NONE = frozenset({None, 'none', False, 'false'})


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def _proxies_message(proxies_raw: str) -> str:
    if not proxies_raw:
        return (
            'MEDIA_API_TRUSTED_PROXIES пуст — X-Forwarded-For игнорируется '
            '(безопасный режим без прокси)'
        )
    return 'MEDIA_API_TRUSTED_PROXIES задан'


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    requirement = control.requirement(level)

    if requirement in _NONE:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'на уровне {level} не требуется',
        )

    proxies_raw = (values.get('MEDIA_API_TRUSTED_PROXIES') or '').strip()

    if requirement == 'required':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=_proxies_message(proxies_raw),
        )

    if requirement == 'required_plus_separate_key':
        internal_key = (values.get('MEDIA_API_INTERNAL_KEY') or '').strip()
        api_secret = (values.get('API_SECRET_KEY') or '').strip()
        if not internal_key:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=(
                    'MEDIA_API_INTERNAL_KEY пуст '
                    f'(уровень {level} требует отдельный ключ internal API)'
                ),
            )
        if api_secret and hmac.compare_digest(internal_key, api_secret):
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=(
                    'MEDIA_API_INTERNAL_KEY совпадает с API_SECRET_KEY '
                    f'(уровень {level} требует отдельный ключ)'
                ),
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=(
                f'{_proxies_message(proxies_raw)}; '
                'MEDIA_API_INTERNAL_KEY задан и отличается от API_SECRET_KEY'
            ),
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='skip',
        message=f'неизвестное требование {requirement!r}',
    )
