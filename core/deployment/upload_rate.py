"""Единый расчёт частоты загрузок для media_api и nginx.

Формат приложения: N/minute (и second|hour|day).
Формат nginx limit_req_zone: Nr/m (или r/s).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from security.profile_defaults import merge_security_profile_defaults

_RATE_LIMIT_SNIPPET = Path(__file__).resolve().parent / 'nginx' / 'snippets' / 'rate_limit.conf'

DEFAULT_UPLOAD_RATE = '30/minute'
DEFAULT_UPLOAD_RATE_ADMIN = '120/minute'
DEFAULT_UPLOAD_RATE_CEILING = '1000/minute'
DEFAULT_UPLOAD_BURST = 25

_MEDIA_RATE_RE = re.compile(
    r'^(\d+)\s*/\s*(second|minute|hour|day|s|m|h|d)$',
    re.IGNORECASE,
)

_UNIT_TO_NGINX = {
    'second': 's',
    's': 's',
    'minute': 'm',
    'm': 'm',
    'hour': 'h',
    'h': 'h',
    'day': 'd',
    'd': 'd',
}


def media_rate_to_nginx(rate: str) -> str:
    """'60/minute' → '60r/m'; при ошибке — из DEFAULT_UPLOAD_RATE_ADMIN."""
    match = _MEDIA_RATE_RE.match((rate or '').strip())
    if not match:
        match = _MEDIA_RATE_RE.match(DEFAULT_UPLOAD_RATE_ADMIN)
        assert match is not None
    count = int(match.group(1))
    unit = _UNIT_TO_NGINX.get(match.group(2).lower(), 'm')
    return f'{count}r/{unit}'


def _rate_to_per_second(rate: str) -> float:
    match = _MEDIA_RATE_RE.match((rate or '').strip())
    if not match:
        return 0.0
    count = int(match.group(1))
    unit = _UNIT_TO_NGINX.get(match.group(2).lower(), 'm')
    seconds = {'s': 1.0, 'm': 60.0, 'h': 3600.0, 'd': 86400.0}.get(unit, 60.0)
    if seconds <= 0:
        return 0.0
    return count / seconds


def higher_media_rate(first: str, second: str) -> str:
    """Частота с большей скоростью (формат N/minute)."""
    first_ok = bool(_MEDIA_RATE_RE.match((first or '').strip()))
    second_ok = bool(_MEDIA_RATE_RE.match((second or '').strip()))
    if first_ok and not second_ok:
        return first.strip()
    if second_ok and not first_ok:
        return second.strip()
    if not first_ok:
        return DEFAULT_UPLOAD_RATE_ADMIN
    if _rate_to_per_second(second) > _rate_to_per_second(first):
        return second.strip()
    return first.strip()


def resolve_upload_rates(values: Mapping[str, Any]) -> dict[str, str | int]:
    """
    Эффективные квоты после merge профиля.

    Returns:
        user_rate, admin_rate, ceiling_rate (строки media), nginx_zone_rate, burst (int).
    """
    merged = merge_security_profile_defaults(values)
    user_rate = (merged.get('MEDIA_API_UPLOAD_RATE') or DEFAULT_UPLOAD_RATE).strip()
    admin_rate = (merged.get('MEDIA_API_UPLOAD_RATE_ADMIN') or DEFAULT_UPLOAD_RATE_ADMIN).strip()
    ceiling_rate = (merged.get('MEDIA_API_UPLOAD_RATE_CEILING') or DEFAULT_UPLOAD_RATE_CEILING).strip()
    if not _MEDIA_RATE_RE.match(user_rate):
        user_rate = DEFAULT_UPLOAD_RATE
    if not _MEDIA_RATE_RE.match(admin_rate):
        admin_rate = DEFAULT_UPLOAD_RATE_ADMIN
    if not _MEDIA_RATE_RE.match(ceiling_rate):
        ceiling_rate = DEFAULT_UPLOAD_RATE_CEILING
    burst_raw = (merged.get('MEDIA_API_UPLOAD_BURST') or '').strip()
    try:
        burst = int(burst_raw) if burst_raw else DEFAULT_UPLOAD_BURST
    except ValueError:
        burst = DEFAULT_UPLOAD_BURST
    if burst < 1:
        burst = DEFAULT_UPLOAD_BURST
    zone_media = higher_media_rate(admin_rate, ceiling_rate)
    return {
        'user_rate': user_rate,
        'admin_rate': admin_rate,
        'ceiling_rate': ceiling_rate,
        'nginx_zone_rate': media_rate_to_nginx(zone_media),
        'burst': burst,
    }


def build_rate_limit_conf(values: Mapping[str, Any]) -> str:
    """Текст зон limit_req для http-контекста nginx (сниппет + частота admin)."""
    rates = resolve_upload_rates(values)
    zone_rate = str(rates['nginx_zone_rate'])
    text = _RATE_LIMIT_SNIPPET.read_text(encoding='utf-8')
    return text.replace('${ERGO_UPLOAD_ZONE_RATE}', zone_rate)


def upload_location_limit_lines(*, burst: int, indent: str = '        ') -> str:
    """Строки limit_req / limit_req_status / limit_conn для location /upload/."""
    return (
        f'{indent}limit_req zone=ergo_upload burst={burst} nodelay;\n'
        f'{indent}limit_req_status 429;\n'
        f'{indent}limit_conn ergo_conn 10;\n'
        f'{indent}limit_conn_status 429;\n'
    )
