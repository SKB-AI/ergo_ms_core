"""
Единые имена OS-служб ERGO MS.

Префикс по умолчанию: ergo_ms (Windows NSSM и Linux systemd).
Изолированные прогоны задают ERGO_SERVICE_PREFIX, чтобы не пересечься
с рабочей установкой.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PREFIX = 'ergo_ms'
PREFIX_ENV = 'ERGO_SERVICE_PREFIX'
_PREFIX_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{1,40}$')

# Старые имена (до унификации) — stop/clean/uninstall только у префикса по умолчанию
LEGACY_SERVICE_NAMES = (
    'ergo-api-dev',
    'ergo-client-dev',
    'ergo-media-api',
    'ergo-celery-beat',
    'ergo-celery-worker',
    'ergo-redis',
    'ergo-postgres',
)

_LEGACY_ROLE = {
    'ergo-api-dev': 'api_dev',
    'ergo-client-dev': 'client_dev',
    'ergo-media-api': 'media_api',
    'ergo-celery-beat': 'celery_beat',
    'ergo-celery-worker': 'celery_worker',
    'ergo-redis': 'redis',
    'ergo-postgres': 'postgres',
    'media_api': 'media_api',
}


def sanitize_prefix(raw: str | None) -> str:
    value = (raw or '').strip()
    if not value:
        return DEFAULT_PREFIX
    if not _PREFIX_RE.match(value):
        return DEFAULT_PREFIX
    return value


def resolve_service_prefix(
    environ: Mapping[str, str] | None = None,
    project_root: Path | None = None,
) -> str:
    if environ is not None:
        from_env = environ.get(PREFIX_ENV)
        if from_env and str(from_env).strip():
            return sanitize_prefix(str(from_env))
    os_value = os.environ.get(PREFIX_ENV)
    if os_value and os_value.strip():
        return sanitize_prefix(os_value)
    if project_root is not None:
        try:
            from env_file_loader import load_project_env
        except ImportError:
            load_project_env = None
        if load_project_env is not None:
            loaded = load_project_env(project_root)
            file_value = loaded.get(PREFIX_ENV)
            if file_value and str(file_value).strip():
                return sanitize_prefix(str(file_value))
    return DEFAULT_PREFIX


def _safe_module_token(name: str) -> str:
    return ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in name)


@dataclass(frozen=True)
class ServiceNames:
    prefix: str = DEFAULT_PREFIX

    def __post_init__(self) -> None:
        object.__setattr__(self, 'prefix', sanitize_prefix(self.prefix))

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        project_root: Path | None = None,
    ) -> ServiceNames:
        return cls(resolve_service_prefix(environ, project_root))

    def name(self, role: str) -> str:
        return f'{self.prefix}_{role}'

    @property
    def api_dev(self) -> str:
        return self.name('api_dev')

    @property
    def client_dev(self) -> str:
        return self.name('client_dev')

    @property
    def media_api(self) -> str:
        return self.name('media_api')

    @property
    def celery_beat(self) -> str:
        return self.name('celery_beat')

    @property
    def celery_worker_base(self) -> str:
        return self.name('celery_worker')

    @property
    def redis(self) -> str:
        return self.name('redis')

    @property
    def meilisearch(self) -> str:
        return self.name('meilisearch')

    @property
    def nginx(self) -> str:
        return self.name('nginx')

    @property
    def postgres(self) -> str:
        return self.name('postgres')

    @property
    def base_services(self) -> tuple[str, ...]:
        return (self.api_dev, self.client_dev, self.media_api, self.celery_beat)

    def celery_worker(self, key: str | None = None) -> str:
        if not key:
            return self.celery_worker_base
        return f'{self.celery_worker_base}_{key}'

    def module(self, module: str, kind: str) -> str:
        return f'{self.prefix}_module_{_safe_module_token(module)}_{kind}'

    def wildcard(self) -> str:
        return f'{self.prefix}_*'

    def matches(self, name: str) -> bool:
        base = name.replace('.service', '')
        return base == self.prefix or base.startswith(f'{self.prefix}_')

    def is_celery_worker(self, name: str) -> bool:
        base = name.replace('.service', '')
        return (
            base == self.celery_worker_base
            or base.startswith(f'{self.celery_worker_base}_')
        )

    def celery_worker_key(self, name: str) -> str | None:
        base = name.replace('.service', '')
        prefix = f'{self.celery_worker_base}_'
        if base.startswith(prefix):
            return base[len(prefix):] or None
        if base == self.celery_worker_base:
            return None
        return None

    def role_of(self, name: str) -> str | None:
        base = name.replace('.service', '')
        token = f'{self.prefix}_'
        if not base.startswith(token):
            return None
        return base[len(token):] or None


_DEFAULT = ServiceNames()

# Совместимость: импорты API_DEV и т.д. остаются именами префикса по умолчанию
API_DEV = _DEFAULT.api_dev
CLIENT_DEV = _DEFAULT.client_dev
MEDIA_API = _DEFAULT.media_api
CELERY_BEAT = _DEFAULT.celery_beat
CELERY_WORKER = _DEFAULT.celery_worker_base
REDIS = _DEFAULT.redis
MEILISEARCH = _DEFAULT.meilisearch
NGINX = _DEFAULT.nginx
POSTGRES = _DEFAULT.postgres
BASE_SERVICES = _DEFAULT.base_services

_LEGACY_EXACT = {
    legacy: _DEFAULT.name(role) for legacy, role in _LEGACY_ROLE.items()
}


def names_from_root(project_root: Path | None = None) -> ServiceNames:
    return ServiceNames.from_env(project_root=project_root)


def celery_worker(key: str | None = None, prefix: str | None = None) -> str:
    return ServiceNames(prefix).celery_worker(key)


def is_celery_worker(name: str, prefix: str | None = None) -> bool:
    base = name.replace('.service', '')
    if ServiceNames(prefix).is_celery_worker(name):
        return True
    return base == 'ergo-celery-worker' or base.startswith('ergo-celery-worker-')


def celery_worker_key(name: str, prefix: str | None = None) -> str | None:
    key = ServiceNames(prefix).celery_worker_key(name)
    if key is not None or ServiceNames(prefix).is_celery_worker(name):
        return key
    base = name.replace('.service', '')
    legacy = 'ergo-celery-worker-'
    if base.startswith(legacy):
        return base[len(legacy):] or None
    if base == 'ergo-celery-worker':
        return None
    return None


def module_service_name(module: str, kind: str, prefix: str | None = None) -> str:
    return ServiceNames(prefix).module(module, kind)


def normalize_service_name(name: str, prefix: str | None = None) -> str:
    """Привести legacy/алиас к текущему имени OS-службы (для logs/status)."""
    if not name:
        return name
    suffix = '.service' if name.endswith('.service') else ''
    base = name[:-len('.service')] if suffix else name
    names = ServiceNames(prefix)

    if base in _LEGACY_EXACT:
        role = _LEGACY_ROLE[base]
        return names.name(role) + suffix

    legacy_worker = 'ergo-celery-worker-'
    if base.startswith(legacy_worker):
        key = base[len(legacy_worker):]
        return names.celery_worker(key) + suffix if key else names.celery_worker_base + suffix

    return name


def _cli_main() -> int:
    parser = argparse.ArgumentParser(description='ERGO MS OS service names')
    parser.add_argument(
        'command',
        choices=('normalize', 'prefix', 'name', 'wildcard'),
    )
    parser.add_argument('value', nargs='?', default='')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--prefix')
    args = parser.parse_args()
    root = args.root.resolve() if args.root else None
    names = ServiceNames(args.prefix) if args.prefix else ServiceNames.from_env(
        project_root=root,
    )
    if args.command == 'normalize':
        if not args.value:
            print('usage: service_names.py normalize <name>', file=sys.stderr)
            return 1
        print(normalize_service_name(args.value, names.prefix), end='')
        return 0
    if args.command == 'prefix':
        print(names.prefix, end='')
        return 0
    if args.command == 'wildcard':
        print(names.wildcard(), end='')
        return 0
    if args.command == 'name':
        if not args.value:
            print('usage: service_names.py name <role>', file=sys.stderr)
            return 1
        role = args.value.strip()
        if role == 'celery_worker':
            print(names.celery_worker_base, end='')
            return 0
        print(names.name(role), end='')
        return 0
    print(f'Неизвестная команда: {args.command}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(_cli_main())
