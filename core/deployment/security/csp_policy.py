"""Режимы Content-Security-Policy (С11 phase 1).

Единый источник строк CSP для API middleware и nginx render.
Режимы: as_is | no_unsafe | no_unsafe_plus_externals.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

FEDERATION_IMPORTMAP_HASHES_REL = Path('core/client/dist/federation-importmap.hashes')

CSP_MODES: tuple[str, ...] = (
    'as_is',
    'no_unsafe',
    'no_unsafe_plus_externals',
)

CSP_MODE_RANK: dict[str, int] = {
    'as_is': 0,
    'no_unsafe': 1,
    'no_unsafe_plus_externals': 2,
}

DEFAULT_CSP_MODE = 'as_is'
CSP_ENV_KEY = 'API_CSP_MODE'

# Карты (Yandex / OSM): для as_is и no_unsafe; на maximum — урезаны (phase 1 stub).
_MAP_SCRIPT = (
    'https://api-maps.yandex.ru https://*.api-maps.yandex.ru '
    'https://yastatic.net https://suggest-maps.yandex.ru'
)
_MAP_STYLE = (
    'https://api-maps.yandex.ru https://*.api-maps.yandex.ru https://yastatic.net'
)
_MAP_CONNECT = (
    'https://api-maps.yandex.ru https://*.api-maps.yandex.ru '
    'https://*.maps.yandex.net https://suggest-maps.yandex.ru https://yastatic.net '
    'https://tile.openstreetmap.org https://*.tile.openstreetmap.org'
)
_MAP_WORKER = (
    'https://api-maps.yandex.ru https://*.api-maps.yandex.ru https://yastatic.net'
)
_MAP_CHILD = 'https://api-maps.yandex.ru'
_MAP_IMG_TILES = (
    'https://*.maps.yandex.net https://tile.openstreetmap.org '
    'https://*.tile.openstreetmap.org'
)


def normalize_csp_mode(raw: str | None) -> str:
    text = (raw or '').strip().lower()
    if text in CSP_MODE_RANK:
        return text
    return DEFAULT_CSP_MODE


def csp_mode_from_values(values: Mapping[str, Any]) -> str:
    """Читает API_CSP_MODE из mapping (после merge профиля, если вызывающий сделал merge)."""
    return normalize_csp_mode(
        None if values.get(CSP_ENV_KEY) is None else str(values.get(CSP_ENV_KEY)),
    )


def resolve_csp_mode(values: Mapping[str, Any]) -> str:
    """Effective режим: merge профиля для unset, затем normalize."""
    from security.profile_defaults import merge_security_profile_defaults

    merged = merge_security_profile_defaults(values)
    return csp_mode_from_values(merged)


def read_federation_importmap_hashes(project_root: Path | None) -> list[str]:
    """sha256-… из client-build для inline import map (без unsafe-inline)."""
    if project_root is None:
        return []
    path = Path(project_root) / FEDERATION_IMPORTMAP_HASHES_REL
    if not path.is_file():
        return []
    hashes: list[str] = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        item = raw.strip()
        if item.startswith('sha256-'):
            hashes.append(item)
    return hashes


def _quote_script_hash(item: str) -> str:
    text = (item or '').strip()
    if text.startswith("'") and text.endswith("'"):
        return text
    return f"'{text}'"


def _with_script_hashes(script: str, extra_script_hashes: Sequence[str] | None) -> str:
    if not extra_script_hashes:
        return script
    quoted = ' '.join(_quote_script_hash(item) for item in extra_script_hashes if item)
    if not quoted:
        return script
    return script.replace("script-src 'self'", f"script-src 'self' {quoted}", 1)


def build_csp_policy(
    mode: str | None = None,
    extra_script_hashes: Sequence[str] | None = None,
) -> str:
    """Строка Content-Security-Policy без кавычек обёртки nginx."""
    resolved = normalize_csp_mode(mode)

    if resolved == 'as_is':
        script = f"script-src 'self' 'unsafe-eval' {_MAP_SCRIPT}"
        style = f"style-src 'self' 'unsafe-inline' {_MAP_STYLE}"
        img = "img-src 'self' data: blob: https:"
        connect = f"connect-src 'self' {_MAP_CONNECT}"
        worker = f"worker-src 'self' blob: data: {_MAP_WORKER}"
        child = f"child-src blob: {_MAP_CHILD}"
    elif resolved == 'no_unsafe':
        # Без unsafe-*; домены карт сохранены (карты могут сломаться — ожидаемо).
        script = f"script-src 'self' {_MAP_SCRIPT}"
        style = f"style-src 'self' {_MAP_STYLE}"
        img = "img-src 'self' data: blob: https:"
        connect = f"connect-src 'self' {_MAP_CONNECT}"
        worker = f"worker-src 'self' blob: data: {_MAP_WORKER}"
        child = f"child-src blob: {_MAP_CHILD}"
    else:
        # no_unsafe_plus_externals (phase 1 stub): без unsafe + без широкого img https:
        # и без внешних script/style/connect карт — полный аудит доменов в phase 2.
        script = "script-src 'self'"
        style = "style-src 'self'"
        img = f"img-src 'self' data: blob: {_MAP_IMG_TILES}"
        connect = "connect-src 'self'"
        worker = "worker-src 'self' blob: data:"
        child = "child-src 'self' blob:"

    parts = [
        "default-src 'self'",
        script,
        style,
        img,
        "font-src 'self' data:",
        connect,
        worker,
        child,
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    script = _with_script_hashes(script, extra_script_hashes)
    parts[1] = script
    return '; '.join(parts)


def build_security_headers_nginx(
    mode: str | None = None,
    extra_script_hashes: Sequence[str] | None = None,
) -> str:
    """Фрагмент nginx add_header (как snippets/security_headers.conf) для режима CSP."""
    csp = build_csp_policy(mode, extra_script_hashes=extra_script_hashes)
    return (
        '# HTTP security headers для SPA, static и proxy (add_header ... always).\n'
        'add_header X-Frame-Options "DENY" always;\n'
        'add_header X-Content-Type-Options "nosniff" always;\n'
        'add_header Referrer-Policy "strict-origin-when-cross-origin" always;\n'
        'add_header Permissions-Policy "accelerometer=(), camera=(), geolocation=(), '
        'gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()" always;\n'
        f'add_header Content-Security-Policy "{csp}" always;\n'
    )


def substitute_security_headers_includes(content: str, headers_block: str) -> str:
    """Подставляет блок заголовков вместо include …/security_headers.conf;"""
    import re

    # После apply_template_replacements путь уже абсолютный; до него — ${ERGO_NGINX_SNIPPETS}.
    pattern = re.compile(
        r'^([ \t]*)include \S+/security_headers\.conf;\s*$',
        re.MULTILINE,
    )

    def _repl(match: re.Match[str]) -> str:
        indent = match.group(1)
        lines = headers_block.strip().splitlines()
        return '\n'.join(
            f'{indent}{line}' if line.strip() else line
            for line in lines
        )

    return pattern.sub(_repl, content)
