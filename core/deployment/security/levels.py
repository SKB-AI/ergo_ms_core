"""Ранги и нормализация уровней безопасности."""

from __future__ import annotations

from ergo_modes import (
    ERGO_SECURITY_VALUES,
    ergo_security,
    ergo_security_enforce,
    ergo_security_is_explicit,
    security_level_rank,
)

LEVEL_ORDER = ('open', 'standard', 'hardened', 'maximum')

__all__ = [
    'ERGO_SECURITY_VALUES',
    'LEVEL_ORDER',
    'ergo_security',
    'ergo_security_enforce',
    'ergo_security_is_explicit',
    'normalize_security_level',
    'security_level_rank',
]


def normalize_security_level(raw: str | None, *, default: str = 'standard') -> str:
    value = (raw or '').strip().lower()
    return value if value in ERGO_SECURITY_VALUES else default
