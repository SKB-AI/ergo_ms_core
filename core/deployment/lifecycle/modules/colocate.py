"""
Соседи ModuleBridge на этой машине: loopback и тот же хост, не HTTP на другой сервер.

Без Django: каталог модулей и карта сервисов читают одни и те же правила.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from urllib.parse import urlparse

_LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})
_WILDCARD_BINDS = frozenset({'0.0.0.0', '::', '*', ''})


def parse_service_urls(raw: str = '') -> dict[str, str]:
    """``<name>=http://host:port,<other>=http://…`` → dict."""
    result: dict[str, str] = {}
    for part in (raw or '').split(','):
        part = part.strip()
        if not part or '=' not in part:
            continue
        name, url = part.split('=', 1)
        name = name.strip()
        url = url.strip().rstrip('/')
        if name and url:
            result[name] = url
    return result


def parse_csv_names(raw: str = '') -> frozenset[str]:
    return frozenset(item.strip() for item in (raw or '').split(',') if item.strip())


def parse_bridge_colocate(raw: str = '', *, transport: str = 'local') -> str:
    """``on`` | ``off``. ``auto`` и пусто — ``on`` при ``BRIDGE_TRANSPORT=http``."""
    value = (raw or '').strip().lower()
    if value in {'on', 'off'}:
        return value
    if value in {'', 'auto'}:
        return 'on' if (transport or '').strip().lower() == 'http' else 'off'
    return 'off'


def url_host(url: str = '') -> str:
    if not url:
        return ''
    return (urlparse(url).hostname or '').strip().lower()


def is_loopback_host(host: str = '') -> bool:
    value = (host or '').strip().lower()
    if not value:
        return False
    if value in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def this_process_hosts(
    environ: Mapping[str, str] | None = None,
    *,
    self_url: str | None = None,
) -> frozenset[str]:
    """Хосты этого процесса: loopback плюс API_HOST и URL самого процесса."""
    env = environ or {}
    hosts: set[str] = set(_LOOPBACK_HOSTS)
    bind = (env.get('API_HOST') or '').strip().lower()
    if bind and bind not in _WILDCARD_BINDS:
        hosts.add(bind)
    if self_url:
        own = url_host(self_url)
        if own:
            hosts.add(own)
    return frozenset(hosts)


def is_colocated_url(
    url: str = '',
    *,
    self_hosts: frozenset[str] | None = None,
) -> bool:
    """URL на этой машине: loopback или тот же хост, что у процесса."""
    peer = url_host(url)
    if not peer:
        return False
    local = self_hosts if self_hosts is not None else frozenset(_LOOPBACK_HOSTS)
    if is_loopback_host(peer):
        return True
    return peer in local


def colocated_module_names(
    *,
    service_urls: Mapping[str, str],
    microservice_modules: frozenset[str] | None = None,
    self_hosts: frozenset[str] | None = None,
) -> frozenset[str]:
    """Имена модулей, которые живут на этой машине.

    URL loopback / тот же хост — сосед здесь. Имя из MICROSERVICE_MODULES
    без URL тоже здесь: это локальный процесс, не запись в карте соседа.
    """
    hosts = self_hosts if self_hosts is not None else frozenset(_LOOPBACK_HOSTS)
    names: set[str] = set()
    for name, url in service_urls.items():
        if is_colocated_url(url, self_hosts=hosts):
            names.add(name)
    for name in microservice_modules or ():
        url = service_urls.get(name, '')
        if not url or is_colocated_url(url, self_hosts=hosts):
            names.add(name)
    return frozenset(names)


def colocated_module_names_from_env(
    environ: Mapping[str, str],
    *,
    self_url: str | None = None,
) -> frozenset[str]:
    if parse_bridge_colocate(
        environ.get('BRIDGE_COLOCATE', ''),
        transport=environ.get('BRIDGE_TRANSPORT', 'local'),
    ) != 'on':
        return frozenset()
    urls = parse_service_urls(environ.get('BRIDGE_SERVICE_URLS', ''))
    return colocated_module_names(
        service_urls=urls,
        microservice_modules=parse_csv_names(environ.get('MICROSERVICE_MODULES', '')),
        self_hosts=this_process_hosts(environ, self_url=self_url),
    )
