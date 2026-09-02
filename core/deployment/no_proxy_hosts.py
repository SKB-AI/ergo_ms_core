"""Хосты, которые не должны уходить в HTTP_PROXY (мост и внутренний LAN)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

_LOOPBACK = ('localhost', '127.0.0.1', '::1')
_PROXY_KEYS = (
    'HTTP_PROXY',
    'HTTPS_PROXY',
    'ALL_PROXY',
    'http_proxy',
    'https_proxy',
    'all_proxy',
)


def _add(host: str, out: list[str], seen: set[str]) -> None:
    item = (host or '').strip().strip('[]')
    if not item or item == '*' or item in ('0.0.0.0', '::'):
        return
    key = item.lower()
    if key in seen:
        return
    seen.add(key)
    out.append(item)


def host_from_url_or_addr(raw: str) -> str:
    text = (raw or '').strip()
    if not text:
        return ''
    if '://' in text:
        return (urlparse(text).hostname or '').strip()
    if text.count(':') == 1 and not text.startswith('['):
        return text.split(':', 1)[0].strip()
    return text


def collect_no_proxy_hosts(values: Mapping[str, str]) -> list[str]:
    """loopback + NO_PROXY из env + хосты моста / nginx / ALLOWED_HOSTS."""
    out: list[str] = []
    seen: set[str] = set()
    for item in _LOOPBACK:
        _add(item, out, seen)
    for key in ('NO_PROXY', 'no_proxy'):
        for part in (values.get(key) or '').split(','):
            _add(part.strip(), out, seen)
    _add(host_from_url_or_addr(values.get('BRIDGE_CORE_URL', '')), out, seen)
    _add(host_from_url_or_addr(values.get('NGINX_API_UPSTREAM', '')), out, seen)
    _add(host_from_url_or_addr(values.get('NGINX_CLIENT_UPSTREAM', '')), out, seen)
    _add(host_from_url_or_addr(values.get('NGINX_CLIENT_REMOTES_UPSTREAM', '')), out, seen)
    _add(host_from_url_or_addr(values.get('NGINX_MEDIA_UPSTREAM', '')), out, seen)
    for part in (values.get('BRIDGE_SERVICE_URLS') or '').split(','):
        if '=' not in part:
            continue
        _add(host_from_url_or_addr(part.split('=', 1)[1]), out, seen)
    for part in (values.get('API_ALLOWED_HOSTS') or '').split(','):
        _add(part.strip(), out, seen)
    return out


def collect_no_proxy_csv(values: Mapping[str, str]) -> str:
    return ','.join(collect_no_proxy_hosts(values))


def collect_no_proxy_csv_for_root(root) -> str:
    from env_file_loader import load_project_env

    return collect_no_proxy_csv(load_project_env(Path(root)))


def collect_outbound_proxy(
    values: Mapping[str, str],
    *,
    fallback: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Исходящий HTTP-прокси для служб и Chrome. Проект перекрывает fallback."""
    merged: dict[str, str] = {}
    for source in (fallback, values):
        if source is None:
            continue
        for key in _PROXY_KEYS:
            raw = (source.get(key) or '').strip()
            if raw:
                merged[key] = raw
    http = merged.get('HTTP_PROXY') or merged.get('http_proxy')
    if http:
        for key in _PROXY_KEYS:
            merged.setdefault(key, http)
    return merged


def first_outbound_proxy_url(
    values: Mapping[str, str] | None = None,
    *,
    fallback: Mapping[str, str] | None = None,
) -> str:
    data = values if values is not None else os.environ
    proxies = collect_outbound_proxy(data, fallback=fallback)
    return (
        proxies.get('HTTPS_PROXY')
        or proxies.get('https_proxy')
        or proxies.get('HTTP_PROXY')
        or proxies.get('http_proxy')
        or proxies.get('ALL_PROXY')
        or proxies.get('all_proxy')
        or ''
    )


def write_systemd_env_file(root, dest) -> None:
    """Записать wrappers/ergo_ms.env: NO_PROXY и исходящий прокси из проекта."""
    from env_file_loader import load_project_env

    root_path = Path(root)
    dest_path = Path(dest)
    values = load_project_env(root_path)
    no_proxy = collect_no_proxy_csv(values)
    lines = [
        '# Environment for ergo_ms services (внутри корня проекта)',
        f'ERGO_ROOT={root_path}',
        'PYTHONUNBUFFERED=1',
        'NODE_ENV=development',
        'ERGO_LOG_CONSOLE=false',
        f'NO_PROXY={no_proxy}',
        f'no_proxy={no_proxy}',
    ]
    for key, value in collect_outbound_proxy(values, fallback=os.environ).items():
        lines.append(f'{key}={value}')
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    import sys

    if len(sys.argv) >= 4 and sys.argv[1] == '--write-systemd-env':
        write_systemd_env_file(sys.argv[2], sys.argv[3])
    else:
        print(collect_no_proxy_csv_for_root(sys.argv[1] if len(sys.argv) > 1 else '.'))
