"""Сравнения значений для контролей kind=value/switch."""

from __future__ import annotations

import re

_RATE_RE = re.compile(
    r'^\s*(\d+)\s*/\s*(second|minute|hour|day)s?\s*$',
    re.IGNORECASE,
)

_PERIOD_SECONDS = {
    'second': 1,
    'minute': 60,
    'hour': 3600,
    'day': 86400,
}


def parse_rate(value: str) -> tuple[int, int] | None:
    """Возвращает (count, period_seconds) или None."""
    match = _RATE_RE.match(str(value or ''))
    if not match:
        return None
    count = int(match.group(1))
    period = _PERIOD_SECONDS[match.group(2).lower().rstrip('s')]
    return count, period


def rate_per_minute(value: str) -> float | None:
    parsed = parse_rate(value)
    if parsed is None:
        return None
    count, period = parsed
    if period <= 0:
        return None
    return count * (60.0 / period)


def is_rate_weaker(actual: str, maximum_allowed: str) -> bool | None:
    """
    True если actual допускает больше запросов в минуту, чем maximum_allowed.
    None если не удалось разобрать.
    """
    a = rate_per_minute(actual)
    m = rate_per_minute(maximum_allowed)
    if a is None or m is None:
        return None
    return a > m


def parse_int(value: object) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def env_truthy(value: str | None) -> bool | None:
    if value is None or str(value).strip() == '':
        return None
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')
