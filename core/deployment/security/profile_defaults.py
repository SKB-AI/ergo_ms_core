"""Подстановка скаляров профиля безопасности для незаданных env-ключей.

Профиль никогда не пишет `.env` — только возвращает копию mapping с дополнениями.
Явный непустой ключ (strip) побеждает; см. `_has_explicit` в ergo_modes.
"""

from __future__ import annotations

from typing import Any, Mapping

from ergo_modes import _has_explicit, ergo_security

from .catalog import load_security_catalog

# Контроли со скалярным env_key, которые Stage 2 может подставить при unset.
APPLYABLE_CONTROL_IDS: tuple[str, ...] = (
    'auth.login_throttle',
    'auth.lockout',
    'password.policy',
    'token.lifetime_required',
    'token.remember_me_max',
    'session.device_retention',
    'media.signed_urls_ttl',
    'media.upload_rate',
    'media.upload_rate_admin',
    'media.content_validation',
    'logging.client_browser',
    'adp.default_role_view_grants',
    'csp.strict',
)

# Check-only: потолок access TTL не инжектим (дефолт кода 30 мин).
_NEVER_INJECT_ENV_KEYS = frozenset({
    'API_ACCESS_TOKEN_LIFETIME',
})


def _requirement_to_env_str(control_id: str, requirement: Any) -> str | None:
    """Ячейка каталога → строка env; None — не подставлять."""
    if requirement is None:
        return None

    if control_id == 'token.lifetime_required':
        # open: optional — не форсить; standard+: true
        if requirement is True or requirement == 'true':
            return 'true'
        return None

    if control_id == 'logging.client_browser':
        if requirement is True or requirement in ('true', 'true_no_pii'):
            return 'true'
        if requirement is False or requirement == 'false':
            return 'false'
        return None

    if isinstance(requirement, bool):
        return 'true' if requirement else 'false'

    if isinstance(requirement, int):
        return str(requirement)

    if isinstance(requirement, float) and requirement == int(requirement):
        return str(int(requirement))

    if isinstance(requirement, str):
        text = requirement.strip()
        if not text:
            return None
        lower = text.lower()
        if lower in ('true', 'false'):
            return lower
        if lower in ('none', 'unlimited'):
            return '0'
        if lower in ('extension', 'extension_and_magic', 'extension_magic_av'):
            return lower
        if lower in ('granted', 'denied'):
            return lower
        if lower in ('as_is', 'no_unsafe', 'no_unsafe_plus_externals'):
            return lower
        if '/' in text:
            return text
        try:
            int(text)
            return text
        except ValueError:
            # Семантические токены (required_outside_dev и т.п.) не инжектим
            return None

    return None


def merge_security_profile_defaults(values: Mapping[str, Any]) -> dict[str, str]:
    """
    Копия values + скаляры профиля для ключей без явного значения.

    Уровень = ergo_security(values) (default standard). Не пишет файлы.
    """
    result: dict[str, str] = {}
    for key, raw in values.items():
        if raw is None:
            continue
        result[str(key)] = str(raw)

    level = ergo_security(result)
    catalog = load_security_catalog()

    for control_id in APPLYABLE_CONTROL_IDS:
        control = catalog.control_by_id(control_id)
        if control is None or not control.env_key:
            continue
        env_key = control.env_key
        if env_key in _NEVER_INJECT_ENV_KEYS:
            continue
        if _has_explicit(result, env_key):
            continue
        injected = _requirement_to_env_str(control_id, control.requirement(level))
        if injected is None:
            continue
        result[env_key] = injected

    return result
