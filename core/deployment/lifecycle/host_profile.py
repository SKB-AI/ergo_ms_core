"""
Роль хоста: какие OS-службы и compose-сервисы ставить.

Без Django и без имён модулей. Читает HOST_PROFILE / HOST_SERVICES и родственные ключи.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _DEPLOYMENT_DIR / 'scripts'
for _entry in (str(_DEPLOYMENT_DIR), str(_SCRIPTS_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

SERVICE_API = 'api'
SERVICE_CLIENT = 'client'
SERVICE_MEDIA = 'media'
SERVICE_BEAT = 'beat'
SERVICE_YAML_WORKERS = 'yaml_workers'
SERVICE_MODULE_API = 'module_api'
SERVICE_MODULE_WORKER = 'module_worker'
SERVICE_MODULE_BEAT = 'module_beat'

KNOWN_SERVICES: FrozenSet[str] = frozenset({
    SERVICE_API,
    SERVICE_CLIENT,
    SERVICE_MEDIA,
    SERVICE_BEAT,
    SERVICE_YAML_WORKERS,
    SERVICE_MODULE_API,
    SERVICE_MODULE_WORKER,
    SERVICE_MODULE_BEAT,
})

PROFILE_FULL = 'full'
PROFILE_CORE = 'core'
PROFILE_MODULES = 'modules'
PROFILE_AUTO = 'auto'

KNOWN_PROFILES: FrozenSet[str] = frozenset({
    PROFILE_FULL,
    PROFILE_CORE,
    PROFILE_MODULES,
    PROFILE_AUTO,
})

_PROFILE_SERVICES: dict[str, FrozenSet[str]] = {
    PROFILE_FULL: frozenset({
        SERVICE_API,
        SERVICE_CLIENT,
        SERVICE_MEDIA,
        SERVICE_BEAT,
        SERVICE_YAML_WORKERS,
        SERVICE_MODULE_API,
        SERVICE_MODULE_WORKER,
        SERVICE_MODULE_BEAT,
    }),
    PROFILE_CORE: frozenset({
        SERVICE_API,
        SERVICE_CLIENT,
        SERVICE_MEDIA,
        SERVICE_BEAT,
        SERVICE_YAML_WORKERS,
    }),
    PROFILE_MODULES: frozenset({
        SERVICE_MODULE_API,
        SERVICE_MODULE_WORKER,
        SERVICE_MODULE_BEAT,
    }),
}

def _core_unit_by_service(prefix: str | None = None) -> dict[str, str]:
    from service_names import ServiceNames

    names = ServiceNames(prefix)
    return {
        SERVICE_API: names.api_dev,
        SERVICE_CLIENT: names.client_dev,
        SERVICE_MEDIA: names.media_api,
        SERVICE_BEAT: names.celery_beat,
    }


# Совместимость: имена префикса по умолчанию
CORE_UNIT_BY_SERVICE: dict[str, str] = _core_unit_by_service()

DOCKER_PROFILE_API = 'host-api'
DOCKER_PROFILE_MEDIA = 'host-media'
DOCKER_PROFILE_BEAT = 'host-beat'

_MEDIA_AUTO = 'auto'


def parse_csv_tokens(raw: str = '') -> frozenset[str]:
    return frozenset(item.strip() for item in (raw or '').split(',') if item.strip())


def _env_str(environ: Mapping[str, str], key: str, default: str = '') -> str:
    raw = environ.get(key, default)
    if raw is None:
        return default
    return str(raw).strip()


def _resolve_profile_name(environ: Mapping[str, str]) -> str:
    raw = _env_str(environ, 'HOST_PROFILE', PROFILE_FULL).lower()
    if raw not in KNOWN_PROFILES:
        return PROFILE_FULL
    if raw != PROFILE_AUTO:
        return raw
    upstream = _env_str(environ, 'NGINX_API_UPSTREAM')
    modules = _env_str(environ, 'MICROSERVICE_MODULES')
    core_url = _env_str(environ, 'BRIDGE_CORE_URL')
    if upstream and modules and core_url:
        return PROFILE_MODULES
    return PROFILE_FULL


def _apply_media(services: set[str], environ: Mapping[str, str]) -> None:
    mode = _env_str(environ, 'HOST_MEDIA').lower()
    if not mode:
        return
    if mode == 'on':
        services.add(SERVICE_MEDIA)
        return
    if mode == 'off':
        services.discard(SERVICE_MEDIA)
        return
    if mode == _MEDIA_AUTO:
        media_mode = _env_str(environ, 'ERGO_MEDIA', 'local').lower()
        if media_mode == 'remote':
            services.discard(SERVICE_MEDIA)


def _apply_celery_workers(services: set[str], environ: Mapping[str, str]) -> None:
    mode = _env_str(environ, 'HOST_CELERY_WORKERS').lower()
    if not mode:
        return
    if mode == 'yaml':
        services.add(SERVICE_YAML_WORKERS)
        services.discard(SERVICE_MODULE_WORKER)
        return
    if mode == 'modules':
        services.discard(SERVICE_YAML_WORKERS)
        services.add(SERVICE_MODULE_WORKER)
        services.add(SERVICE_MODULE_BEAT)
        return
    if mode == 'none':
        services.discard(SERVICE_YAML_WORKERS)
        services.discard(SERVICE_MODULE_WORKER)
        services.discard(SERVICE_MODULE_BEAT)


@dataclass(frozen=True)
class HostProfile:
    name: str
    services: FrozenSet[str]
    prefix: str = 'ergo_ms'

    def wants(self, service_id: str) -> bool:
        return service_id in self.services

    def core_unit_names(self) -> tuple[str, ...]:
        mapping = _core_unit_by_service(self.prefix)
        names = [
            mapping[key]
            for key in (SERVICE_API, SERVICE_CLIENT, SERVICE_MEDIA, SERVICE_BEAT)
            if self.wants(key)
        ]
        return tuple(names)

    def docker_compose_profiles(self) -> tuple[str, ...]:
        names: list[str] = []
        if self.wants(SERVICE_API):
            names.append(DOCKER_PROFILE_API)
        if self.wants(SERVICE_MEDIA):
            names.append(DOCKER_PROFILE_MEDIA)
        if self.wants(SERVICE_BEAT):
            names.append(DOCKER_PROFILE_BEAT)
        return tuple(names)

    def as_dict(self) -> dict[str, object]:
        return {
            'profile': self.name,
            'services': sorted(self.services),
            'core_units': list(self.core_unit_names()),
            'docker_profiles': list(self.docker_compose_profiles()),
            'wants_yaml_workers': self.wants(SERVICE_YAML_WORKERS),
            'wants_module_api': self.wants(SERVICE_MODULE_API),
            'wants_module_worker': self.wants(SERVICE_MODULE_WORKER),
            'wants_module_beat': self.wants(SERVICE_MODULE_BEAT),
        }


def resolve_host_profile(environ: Mapping[str, str]) -> HostProfile:
    name = _resolve_profile_name(environ)
    override = parse_csv_tokens(_env_str(environ, 'HOST_SERVICES'))
    if override:
        services = {item.lower() for item in override if item.lower() in KNOWN_SERVICES}
    else:
        services = set(_PROFILE_SERVICES[name])
    _apply_media(services, environ)
    _apply_celery_workers(services, environ)
    from service_names import resolve_service_prefix

    return HostProfile(
        name=name,
        services=frozenset(services),
        prefix=resolve_service_prefix(environ),
    )


def resolve_host_profile_from_root(
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> HostProfile:
    from env_file_loader import load_project_env

    values = dict(load_project_env(project_root))
    overlay = environ if environ is not None else {}
    for key, val in overlay.items():
        if val is not None and str(val).strip() != '':
            values[key] = str(val).strip()
    return resolve_host_profile(values)


def _ensure_import_path() -> None:
    deployment = Path(__file__).resolve().parent.parent
    entry = str(deployment)
    if entry not in sys.path:
        sys.path.insert(0, entry)


def main(argv: list[str] | None = None) -> int:
    _ensure_import_path()
    parser = argparse.ArgumentParser(description='Host profile for OS services')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--core-units', action='store_true')
    parser.add_argument('--wants', metavar='SERVICE')
    args = parser.parse_args(argv)

    root = args.root.resolve()
    profile = resolve_host_profile_from_root(root)
    if args.wants:
        return 0 if profile.wants(args.wants.strip().lower()) else 1
    if args.core_units:
        for name in profile.core_unit_names():
            print(name)
        return 0
    if args.json:
        print(json.dumps(profile.as_dict(), ensure_ascii=False))
        return 0
    print(profile.name)
    print(','.join(sorted(profile.services)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
