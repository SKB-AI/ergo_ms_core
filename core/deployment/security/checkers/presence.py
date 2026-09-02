"""Проверка непустых списков origins вне development."""

from __future__ import annotations

from typing import Any

from ergo_modes import effective_deploy_type
from security.catalog import Control, SecurityCatalog
from security.levels import security_level_rank
from security.report import Finding


def nonempty_outside_dev(
    control: Control,
    catalog: SecurityCatalog,
    context: dict[str, Any],
) -> Finding:
    values: dict[str, str] = context['values']
    level = context['level']
    deploy = effective_deploy_type(values)
    env_key = control.env_key or ''
    raw = (values.get(env_key) or '').strip()
    requirement = str(control.requirement(level) or '')
    rank = security_level_rank(level)

    always = requirement in {
        'always_required',
        'always_required_no_regex',
    } or (rank >= 3 and control.id == 'csrf.trusted_origins')

    # maximum CORS: regex alone not enough — if only regexes set, still warn/error
    if control.id == 'cors.explicit_origins' and requirement == 'always_required_no_regex':
        if raw:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='ok',
                message=f'{env_key} задан',
            )
        regexes = (values.get('CORS_ALLOWED_ORIGIN_REGEXES') or '').strip()
        if regexes:
            sev = 'error' if control.violation == 'error' else 'warning'
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=sev,
                message='на maximum нужны явные origins; regex-шаблоны запрещены профилем',
            )
        sev = 'error' if control.violation == 'error' else 'warning'
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=sev,
            message=f'{env_key} пуст (уровень {level} требует явный список)',
        )

    if always:
        if raw:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='ok',
                message=f'{env_key} задан',
            )
        # CORS may use regexes as alternative on standard/hardened
        if control.id == 'cors.explicit_origins':
            regexes = (values.get('CORS_ALLOWED_ORIGIN_REGEXES') or '').strip()
            if regexes:
                return Finding(
                    control_id=control.id,
                    title=control.title,
                    severity='ok',
                    message='CORS_ALLOWED_ORIGIN_REGEXES задан',
                )
        sev = 'error' if control.violation == 'error' else 'warning'
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=sev,
            message=f'{env_key} пуст (уровень {level} требует список всегда)',
        )

    if requirement in {'dev_defaults', 'optional'} or deploy == 'development':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='development: пустой список допустим',
        )

    # required_outside_dev
    if raw:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'{env_key} задан',
        )

    if control.id == 'cors.explicit_origins':
        regexes = (values.get('CORS_ALLOWED_ORIGIN_REGEXES') or '').strip()
        if regexes:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='ok',
                message='CORS_ALLOWED_ORIGIN_REGEXES задан',
            )

    sev = 'error' if control.violation == 'error' else 'warning'
    return Finding(
        control_id=control.id,
        title=control.title,
        severity=sev,
        message=f'{env_key} пуст при ERGO_ENV={deploy}',
    )
