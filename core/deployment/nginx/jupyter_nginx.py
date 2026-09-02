"""
Рендер блоков nginx для Jupyter (stdlib, без Django).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SNIPPETS_DIR = Path(__file__).resolve().parent / 'snippets'
_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from ergo_modes import effective_jupyter_behind_nginx  # noqa: E402


def _normalize_base_path(raw: str) -> str:
    path = (raw or '/jupyter/').strip() or '/jupyter/'
    if not path.startswith('/'):
        path = f'/{path}'
    if not path.endswith('/'):
        path = f'{path}/'
    return path


def jupyter_nginx_enabled(values: dict[str, str]) -> bool:
    return effective_jupyter_behind_nginx(values)


def resolve_jupyter_bind_port(values: dict[str, str], default: str = '8002') -> str:
    explicit = values.get('API_JUPYTER_BIND_PORT', '').strip()
    return explicit or default


def resolve_jupyter_base_path(values: dict[str, str]) -> str:
    return _normalize_base_path(values.get('API_JUPYTER_BASE_PATH', '/jupyter/'))


def render_jupyter_upstream_block(values: dict[str, str]) -> str:
    if not jupyter_nginx_enabled(values):
        return ''
    snippet_path = _SNIPPETS_DIR / 'jupyter_upstream.conf'
    if not snippet_path.is_file():
        return ''
    bind_port = resolve_jupyter_bind_port(values)
    return snippet_path.read_text(encoding='utf-8').replace('${ERGO_JUPYTER_BIND_PORT}', bind_port)


def render_jupyter_location_block(values: dict[str, str]) -> str:
    if not jupyter_nginx_enabled(values):
        return ''
    snippet_path = _SNIPPETS_DIR / 'jupyter_location.conf'
    if not snippet_path.is_file():
        return ''
    base_path = resolve_jupyter_base_path(values)
    return snippet_path.read_text(encoding='utf-8').replace('${ERGO_JUPYTER_BASE_PATH}', base_path)


def resolve_jupyter_vars(values: dict[str, str]) -> dict[str, str]:
    """Effective Jupyter-переменные для deployment (read-only)."""
    enabled = jupyter_nginx_enabled(values)
    bind_port = resolve_jupyter_bind_port(values)
    base_path = resolve_jupyter_base_path(values)
    resolved: dict[str, str] = {
        'API_JUPYTER_BEHIND_NGINX_EFFECTIVE': 'true' if enabled else 'false',
        'API_JUPYTER_BIND_PORT': bind_port,
        'API_JUPYTER_BASE_PATH': base_path,
    }
    if enabled:
        resolved['ERGO_JUPYTER_UPSTREAM'] = 'enabled'
        resolved['ERGO_JUPYTER_LOCATION'] = 'enabled'
    return resolved
