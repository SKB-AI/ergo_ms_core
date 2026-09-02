"""
Расчёт лимитов загрузки для nginx и диагностики.

MEDIA_UPLOAD_MAX_SIZE — дефолт (если модуль не запросил больше).
MEDIA_UPLOAD_HARD_MAX_SIZE — абсолютный потолок (модули могут быть выше дефолта).
Direct-upload (tasks и др.) + module ceilings → client_max_body_size с запасом.
Stdlib, без Django.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

DEFAULT_MEDIA_UPLOAD_BYTES = 524_288_000  # 500 MiB
DEFAULT_HARD_MAX_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB
DEFAULT_MARGIN_PERCENT = 10
DEFAULT_TASKS_ATTACHMENT_MB = 600

# Явный реестр direct-upload лимитов (значение в МБ → байты)
DIRECT_UPLOAD_LIMIT_KEYS: tuple[tuple[str, int], ...] = (
    ('TASKS_MAX_ATTACHMENT_SIZE_MB', DEFAULT_TASKS_ATTACHMENT_MB),
)

# Известные feature-лимиты клиента ядра (для отчёта check; байты)
KNOWN_FEATURE_LIMITS_BYTES: dict[str, int] = {
    'avatar': 5 * 1024 * 1024,
    'messengerAttachment': 25 * 1024 * 1024,
}

# Env media-лимиты (МБ). Имена модулей не хардкодим — только CLIENT_* ключи из .env.
# zero_means_hard: 0 / пусто при default 0 → MEDIA_UPLOAD_HARD_MAX_SIZE
# (env_key, default_mb, zero_means_hard)
MODULE_MEDIA_LIMIT_KEYS: tuple[tuple[str, int, bool], ...] = (
    ('CLIENT_BI_UPLOAD_MAX_SIZE_MB', 200, False),
    ('CLIENT_VIDEO_UPLOAD_MAX_SIZE_MB', 0, True),
)


def _env_str(env: Mapping[str, str], key: str, default: str = '') -> str:
    return (env.get(key) or default).strip()


def _parse_positive_int(raw: str, default: int) -> int:
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def parse_media_upload_bytes(env: Mapping[str, str]) -> int:
    """MEDIA_UPLOAD_MAX_SIZE в байтах (дефолт 500 MiB)."""
    return _parse_positive_int(
        _env_str(env, 'MEDIA_UPLOAD_MAX_SIZE'),
        DEFAULT_MEDIA_UPLOAD_BYTES,
    )


def parse_hard_max_bytes(env: Mapping[str, str]) -> int:
    """MEDIA_UPLOAD_HARD_MAX_SIZE; не ниже дефолтного MEDIA_UPLOAD_MAX_SIZE."""
    default_media = parse_media_upload_bytes(env)
    hard = _parse_positive_int(
        _env_str(env, 'MEDIA_UPLOAD_HARD_MAX_SIZE'),
        DEFAULT_HARD_MAX_BYTES,
    )
    return max(hard, default_media)


def parse_margin_percent(env: Mapping[str, str]) -> int:
    """NGINX_UPLOAD_BODY_MARGIN_PERCENT (дефолт 10)."""
    return _parse_positive_int(
        _env_str(env, 'NGINX_UPLOAD_BODY_MARGIN_PERCENT'),
        DEFAULT_MARGIN_PERCENT,
    )


def parse_direct_upload_bytes(env: Mapping[str, str]) -> int:
    """Максимум среди зарегистрированных direct-upload лимитов (байты)."""
    max_bytes = 0
    for key, default_mb in DIRECT_UPLOAD_LIMIT_KEYS:
        mb = _parse_positive_int(_env_str(env, key), default_mb)
        max_bytes = max(max_bytes, mb * 1024 * 1024)
    return max_bytes


def list_direct_upload_limits(env: Mapping[str, str]) -> list[tuple[str, int]]:
    """Список (env_key, bytes) для отчёта."""
    result: list[tuple[str, int]] = []
    for key, default_mb in DIRECT_UPLOAD_LIMIT_KEYS:
        mb = _parse_positive_int(_env_str(env, key), default_mb)
        result.append((key, mb * 1024 * 1024))
    return result


def parse_module_ceiling_bytes(
    env: Mapping[str, str],
    key: str,
    default_mb: int,
    *,
    zero_means_hard: bool,
) -> int:
    """Лимит модуля в байтах (может быть выше MEDIA_UPLOAD_MAX_SIZE, ≤ hard)."""
    hard = parse_hard_max_bytes(env)
    raw = _env_str(env, key)
    if zero_means_hard:
        if raw == '' and default_mb == 0:
            return hard
        if raw == '0':
            return hard
    if raw == '':
        mb = default_mb
    else:
        try:
            mb = int(raw)
        except (TypeError, ValueError):
            mb = default_mb
        if mb < 0:
            mb = default_mb
        if zero_means_hard and mb == 0:
            return hard
    if mb <= 0:
        return hard if zero_means_hard else min(default_mb * 1024 * 1024, hard)
    return min(mb * 1024 * 1024, hard)


def parse_modules_max_bytes(env: Mapping[str, str]) -> int:
    max_bytes = 0
    for key, default_mb, zero_means_hard in MODULE_MEDIA_LIMIT_KEYS:
        max_bytes = max(
            max_bytes,
            parse_module_ceiling_bytes(
                env, key, default_mb, zero_means_hard=zero_means_hard,
            ),
        )
    return max_bytes


def compute_client_max_body_bytes(env: Mapping[str, str]) -> int:
    """
    Максимальный размер тела запроса для nginx:
    max(hard media, module ceilings, direct uploads) + margin%.
    """
    hard = parse_hard_max_bytes(env)
    modules = parse_modules_max_bytes(env)
    direct = parse_direct_upload_bytes(env)
    # hard покрывает дефолтный MEDIA_UPLOAD_MAX_SIZE и модули с zero_means_hard
    base = max(hard, modules, direct)
    margin = parse_margin_percent(env)
    with_margin = math.ceil(base * (100 + margin) / 100)
    return max(with_margin, 1)


def format_nginx_body_size(bytes_val: int) -> str:
    """
    Формат nginx client_max_body_size: целые m или g.
    Округляет вверх до целых мебибайт (минимум 1m).
    """
    if bytes_val <= 0:
        return '1m'
    mib = math.ceil(bytes_val / (1024 * 1024))
    if mib >= 1024 and mib % 1024 == 0:
        return f'{mib // 1024}g'
    if mib >= 1024:
        return f'{mib}m'
    return f'{max(mib, 1)}m'


def format_mib(bytes_val: int) -> str:
    """Человекочитаемый размер в MiB/GiB (для CLI)."""
    gib = bytes_val / (1024 * 1024 * 1024)
    if gib >= 1 and abs(gib - round(gib)) < 0.05:
        return f'{int(round(gib))} GiB'
    if gib >= 1:
        return f'{gib:.1f} GiB'
    mib = bytes_val / (1024 * 1024)
    if abs(mib - round(mib)) < 0.05:
        return f'{int(round(mib))} MiB'
    return f'{mib:.1f} MiB'


def build_upload_limits_report(env: Mapping[str, str]) -> dict[str, Any]:
    """Структурированный отчёт для upload-limits-check."""
    media_bytes = parse_media_upload_bytes(env)
    hard_bytes = parse_hard_max_bytes(env)
    direct_items = list_direct_upload_limits(env)
    body_bytes = compute_client_max_body_bytes(env)
    nginx_size = format_nginx_body_size(body_bytes)

    module_ok: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    for key, default_mb, zero_means_hard in MODULE_MEDIA_LIMIT_KEYS:
        module_bytes = parse_module_ceiling_bytes(
            env, key, default_mb, zero_means_hard=zero_means_hard,
        )
        ok = module_bytes <= hard_bytes
        above_default = module_bytes > media_bytes
        entry = {
            'module': key,
            'key': key,
            'bytes': module_bytes,
            'ok': ok,
            'above_default': above_default,
        }
        module_ok.append(entry)
        if not ok:
            errors.append(
                f'{key} превышает MEDIA_UPLOAD_HARD_MAX_SIZE '
                f'({format_mib(hard_bytes)})'
            )
        elif above_default:
            warnings.append(
                f'{key}: {format_mib(module_bytes)} выше дефолта '
                f'MEDIA_UPLOAD_MAX_SIZE ({format_mib(media_bytes)}), '
                f'в пределах hard ({format_mib(hard_bytes)})'
            )

    return {
        'media_bytes': media_bytes,
        'hard_bytes': hard_bytes,
        'direct_limits': direct_items,
        'margin_percent': parse_margin_percent(env),
        'body_bytes': body_bytes,
        'nginx_size': nginx_size,
        'modules': module_ok,
        'warnings': warnings,
        'errors': errors,
        'features': dict(KNOWN_FEATURE_LIMITS_BYTES),
    }
