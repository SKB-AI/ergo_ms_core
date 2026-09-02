"""Проверка MEDIA_API_CONTENT_VALIDATION vs профиль (С5)."""

from __future__ import annotations

from typing import Any

from security.catalog import Control, SecurityCatalog
from security.report import Finding

# Ранги режимов: слабее профиля → error (кроме честного AV-defer).
_MODE_RANK = {
    'extension': 0,
    'extension_and_magic': 1,
    'extension_magic_av': 2,
}

_DEFAULT_MODE = 'extension'


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def _normalize_mode(raw: str | None) -> str:
    text = (raw or '').strip().lower()
    if text in _MODE_RANK:
        return text
    return _DEFAULT_MODE


def _av_scanner_configured(values: dict[str, Any]) -> bool:
    """Реальный сканер пока не подключён; env-флаг на будущее."""
    flag = (values.get('MEDIA_API_AV_SCANNER') or '').strip().lower()
    return flag in {'1', 'true', 'yes', 'clamav'}


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    required = _normalize_mode(str(control.requirement(level) or _DEFAULT_MODE))
    env_key = control.env_key or 'MEDIA_API_CONTENT_VALIDATION'
    raw = values.get(env_key)
    if raw is None or str(raw).strip() == '':
        # APPLYABLE: unset → runtime merge подставит требование профиля
        actual = required
        source = 'профиль (ключ не задан)'
    else:
        actual = _normalize_mode(str(raw))
        source = env_key

    req_rank = _MODE_RANK[required]
    act_rank = _MODE_RANK[actual]

    if act_rank < req_rank:
        # maximum требует AV, а задан только magic — не false OK, warning (AV phase 2).
        if required == 'extension_magic_av' and act_rank >= _MODE_RANK['extension_and_magic']:
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='warning',
                message=(
                    f'задано {actual} ({source}); уровень {level} требует {required}, '
                    'антивирусная проверка (phase 2) ещё не подключена'
                ),
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'задано {actual} ({source}), уровень {level} требует не слабее {required}',
        )

    if actual == 'extension_magic_av' or required == 'extension_magic_av':
        if not _av_scanner_configured(values):
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='skip',
                message=(
                    f'режим {actual} (требуется {required}): AV-сканер не настроен '
                    '(MEDIA_API_AV_SCANNER); phase 2 stub — не считаем OK'
                ),
            )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{actual} соответствует минимуму {required} ({source})',
    )
