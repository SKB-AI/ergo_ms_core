"""Каталог служб ERGO MS."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / 'scripts'
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from service_names import ServiceNames, celery_worker  # noqa: E402

CORE_SERVICE_IDS = ('api', 'client', 'media', 'beat')


@dataclass(frozen=True)
class ServiceEntry:
    service_id: str
    unit_name: str
    install_op: str
    optional: bool = False


def _load_workers(project_root: Path, prefix: str | None = None) -> list[ServiceEntry]:
    path = project_root / 'celery_workers.yaml'
    if not path.is_file():
        return [ServiceEntry('worker-all', celery_worker('all', prefix), 'install-workers')]
    try:
        import yaml  # noqa: WPS433

        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        workers = data.get('workers') or {}
        entries: list[ServiceEntry] = []
        for key in workers:
            entries.append(
                ServiceEntry(
                    f'worker-{key}',
                    celery_worker(str(key), prefix),
                    'install-workers',
                )
            )
        return entries or [ServiceEntry('worker-all', celery_worker('all', prefix), 'install-workers')]
    except Exception:
        return [ServiceEntry('worker-all', celery_worker('all', prefix), 'install-workers')]


def list_core_services(prefix: str | None = None) -> list[ServiceEntry]:
    names = ServiceNames(prefix)
    return [
        ServiceEntry('api', names.api_dev, 'install-api'),
        ServiceEntry('client', names.client_dev, 'install-client', optional=True),
        ServiceEntry('media', names.media_api, 'install-media'),
        ServiceEntry('beat', names.celery_beat, 'install-beat'),
    ]


def resolve_service_catalog(project_root: Path, disabled_modules: set[str]) -> list[ServiceEntry]:
    from lifecycle.host_profile import (  # noqa: WPS433
        SERVICE_API,
        SERVICE_BEAT,
        SERVICE_CLIENT,
        SERVICE_MEDIA,
        SERVICE_YAML_WORKERS,
        resolve_host_profile_from_root,
    )

    profile = resolve_host_profile_from_root(project_root)
    id_map = {
        'api': SERVICE_API,
        'client': SERVICE_CLIENT,
        'media': SERVICE_MEDIA,
        'beat': SERVICE_BEAT,
    }
    catalog = [
        entry
        for entry in list_core_services(profile.prefix)
        if profile.wants(id_map[entry.service_id])
    ]
    if profile.wants(SERVICE_YAML_WORKERS):
        catalog.extend(_load_workers(project_root, profile.prefix))
    return catalog
