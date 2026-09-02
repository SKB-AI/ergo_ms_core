"""Проверка API_SECRET_KEY для production без печати значения."""

from __future__ import annotations

import sys
from pathlib import Path

# Каталог refs.insecure_secret_values + legacy aliases media/API.
INSECURE_SECRET_VALUES: frozenset[str] = frozenset({
    '',
    'secret_key',
    'changeme',
    'django-insecure',
    'secret-key',
    'media-api-insecure-key',
})

MIN_PRODUCTION_SECRET_LENGTH = 32

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))


def _t(key: str, **params: object) -> str:
    from cli_locale import t

    return t(key, **params)


def is_insecure_secret(secret_key: str | None) -> bool:
    key = (secret_key or '').strip()
    if not key:
        return True
    if key in INSECURE_SECRET_VALUES:
        return True
    return False


def production_secret_is_valid(secret_key: str | None) -> bool:
    key = (secret_key or '').strip()
    if is_insecure_secret(key):
        return False
    return len(key) >= MIN_PRODUCTION_SECRET_LENGTH


def validate_production_secret_key(secret_key: str | None) -> None:
    """
    Fail-fast для ERGO_ENV=production.

    Raises:
        ValueError: пустой, шаблонный или слишком короткий ключ (без значения в тексте).
    """
    key = (secret_key or '').strip()
    if not key:
        raise ValueError(_t('production_secret_empty'))
    if key in INSECURE_SECRET_VALUES:
        raise ValueError(_t('production_secret_insecure'))
    if len(key) < MIN_PRODUCTION_SECRET_LENGTH:
        raise ValueError(
            _t('production_secret_too_short', min_length=MIN_PRODUCTION_SECRET_LENGTH)
        )
