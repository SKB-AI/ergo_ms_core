"""Проверки скалярных env-значений."""

from __future__ import annotations

from typing import Any

from ergo_modes import effective_deploy_type
from security.catalog import Control, SecurityCatalog
from security.compare import env_truthy, is_rate_weaker, parse_int
from security.levels import security_level_rank
from security.report import Finding


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def env_rate_max(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    required = control.requirement(level)
    env_key = control.env_key
    if not env_key:
        return Finding(control.id, 'skip', 'нет env_key', title=control.title)

    raw = (values.get(env_key) or '').strip()
    if not raw:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='info',
            message=f'{env_key} не задан — используется значение по умолчанию в коде',
        )

    weaker = is_rate_weaker(raw, str(required))
    if weaker is None:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='skip',
            message=f'не удалось сравнить rate: {raw!r} vs {required!r}',
        )
    if weaker:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано {raw}, уровень {level} требует не более {required}',
        )
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{raw} соответствует максимуму {required}',
    )


def env_int_max(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    required = control.requirement(level)
    env_key = control.env_key
    if not env_key:
        return Finding(control.id, 'skip', 'нет env_key', title=control.title)

    max_allowed = parse_int(required)
    if max_allowed is None:
        # none / unlimited / семантика без числа
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'на уровне {level} верхняя граница не задана',
        )

    if max_allowed == 0 and control.id == 'token.remember_me_max':
        # maximum: remember-me запрещён — если ключ > 0, violation
        raw = (values.get(env_key) or '').strip()
        if not raw:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='ok',
                message='ключ не задан (remember-me по профилю запрещён — проверьте код/дефолт)',
            )
        actual = parse_int(raw)
        if actual is not None and actual > 0:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=f'задано {actual} мин, уровень {level} запрещает remember-me',
            )
        return Finding(control.id, 'ok', 'remember-me выключен', title=control.title)

    raw = (values.get(env_key) or '').strip()
    if not raw:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='info',
            message=f'{env_key} не задан — используется дефолт кода',
        )
    actual = parse_int(raw)
    if actual is None:
        return Finding(control.id, 'skip', f'нецелое значение {env_key}', title=control.title)

    # Lockout / retention: 0 = выкл., слабее положительного требования профиля.
    if (
        max_allowed > 0
        and actual == 0
        and control.id in ('auth.lockout', 'session.device_retention')
    ):
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано 0 (выкл.), уровень {level} требует не более {max_allowed}',
        )

    if actual > max_allowed:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано {actual}, уровень {level} требует не более {max_allowed}',
        )
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{actual} <= {max_allowed}',
    )


def env_int_min(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    required = control.requirement(level)
    env_key = control.env_key
    if not env_key:
        return Finding(control.id, 'skip', 'нет env_key', title=control.title)

    min_required = parse_int(required)
    if min_required is None:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'на уровне {level} минимум не задан',
        )

    raw = (values.get(env_key) or '').strip()
    if not raw:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='info',
            message=f'{env_key} не задан — используется дефолт кода',
        )
    actual = parse_int(raw)
    if actual is None:
        return Finding(control.id, 'skip', f'нецелое значение {env_key}', title=control.title)
    if actual < min_required:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано {actual}, уровень {level} требует не менее {min_required}',
        )
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{actual} >= {min_required}',
    )


def env_bool_required_true(
    control: Control,
    catalog: SecurityCatalog,
    context: dict[str, Any],
) -> Finding:
    values = context['values']
    level = context['level']
    requirement = control.requirement(level)
    env_key = control.env_key or 'API_JWT_LIFETIME_ENABLED'

    if requirement in ('optional', False, 'false') or security_level_rank(level) <= 0:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'на уровне {level} отключение срока допускается',
        )

    raw = values.get(env_key)
    flag = env_truthy(raw if raw is not None else 'true')
    # unset → код default True
    if raw is None or str(raw).strip() == '':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'{env_key} не задан (дефолт кода: включено)',
        )
    if flag is False:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'{env_key}=false запрещён для уровня {level}',
        )
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{env_key} включён',
    )


def registration_mode_min(
    control: Control,
    catalog: SecurityCatalog,
    context: dict[str, Any],
) -> Finding:
    values = context['values']
    level = context['level']
    requirement = str(control.requirement(level) or 'any')
    mode = (values.get(control.env_key or 'API_REGISTRATION_MODE') or 'open').strip().lower()

    if requirement == 'any':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'режим {mode} допустим на {level}',
        )
    if requirement == 'invitation_or_closed':
        if mode in {'invitation', 'closed'}:
            return Finding(control.id, 'ok', f'{mode}', title=control.title)
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано {mode}, уровень {level} требует invitation или closed',
        )
    if requirement == 'closed':
        if mode == 'closed':
            return Finding(control.id, 'ok', 'closed', title=control.title)
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано {mode}, уровень {level} требует closed',
        )
    return Finding(control.id, 'skip', f'неизвестное требование {requirement}', title=control.title)


def reset_code_policy(
    control: Control,
    catalog: SecurityCatalog,
    context: dict[str, Any],
) -> Finding:
    values = context['values']
    level = context['level']
    requirement = str(control.requirement(level) or '')
    if requirement in {'unrestricted', 'admin_only'}:
        if requirement == 'admin_only':
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='skip',
                message='admin_only пока не проверяется автоматически',
            )
        return Finding(control.id, 'ok', 'на open ограничения не требуются', title=control.title)

    ttl = parse_int(values.get('API_PASSWORD_RESET_CODE_TTL_MINUTES') or '15')
    attempts = parse_int(values.get('API_PASSWORD_RESET_CODE_MAX_ATTEMPTS') or '5')
    if requirement == 'ttl15_attempts5':
        max_ttl, max_attempts = 15, 5
    elif requirement == 'ttl10_attempts3':
        max_ttl, max_attempts = 10, 3
    else:
        return Finding(control.id, 'skip', f'неизвестное требование {requirement}', title=control.title)

    problems = []
    if ttl is not None and ttl > max_ttl:
        problems.append(f'TTL={ttl}>{max_ttl}')
    if attempts is not None and attempts > max_attempts:
        problems.append(f'attempts={attempts}>{max_attempts}')
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
        message=f'TTL<={max_ttl}, attempts<={max_attempts}',
    )


def docs_exposure(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    deploy = effective_deploy_type(values)
    requirement = str(control.requirement(level) or '')
    swagger = env_truthy(values.get('API_SWAGGER_ENABLED'))
    browsable = env_truthy(values.get('API_DRF_BROWSABLE_ENABLED'))

    if requirement == 'open':
        return Finding(control.id, 'ok', 'на open документация может быть открыта', title=control.title)

    if deploy == 'development' and requirement == 'closed_outside_dev':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='development: документация может быть включена',
        )

    # production or hardened/maximum closed
    forced_on = swagger is True or browsable is True
    if forced_on and requirement in {'closed_outside_dev', 'closed', 'unregistered'}:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message='Swagger/DRF browsable явно включены вне допустимого режима',
        )
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message='документация не форсирована во включённое состояние',
    )
