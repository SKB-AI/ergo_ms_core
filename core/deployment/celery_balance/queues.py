"""Глубина очередей Celery (Redis LLEN; database/local — unknown)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import yaml

from celery_balance.constants import (
    QUEUES_CACHE_NAME,
    REDIS_CELERY_BROKER_DB_DEFAULT,
    SIGNATURE_SEPARATOR,
)
from env_file_loader import load_project_env
from project_layout import cache_dir


@dataclass(frozen=True)
class QueueSnapshot:
    name: str
    depth: int | None
    source: str


@dataclass(frozen=True)
class QueuesReport:
    broker: str
    queues: tuple[QueueSnapshot, ...]
    known: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            'broker': self.broker,
            'known': self.known,
            'queues': [
                {'name': item.name, 'depth': item.depth, 'source': item.source}
                for item in self.queues
            ],
        }


def _read_bin_payload(path: Path) -> Any | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if SIGNATURE_SEPARATOR in raw:
        payload = raw.split(SIGNATURE_SEPARATOR, 1)[0]
    else:
        payload = raw
    try:
        return json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def load_queue_names(project_root: Path) -> list[str]:
    data = _read_bin_payload(cache_dir(project_root) / QUEUES_CACHE_NAME)
    names: list[str] = []
    if isinstance(data, dict):
        raw = data.get('queues')
        if isinstance(raw, list):
            names = [str(item) for item in raw if item]
        elif isinstance(raw, dict):
            names = [str(key) for key in raw.keys()]
    if 'default' not in names:
        names.append('default')
    return sorted(set(names))


def _broker_kind(environ: dict[str, str]) -> str:
    backend = (environ.get('CELERY_BROKER_BACKEND') or 'auto').strip().lower()
    ergo = (environ.get('ERGO_BROKER') or '').strip().lower()
    explicit = (environ.get('CELERY_BROKER_URL') or '').strip().lower()
    if explicit.startswith('redis://') or explicit.startswith('rediss://'):
        return 'redis'
    if backend == 'redis':
        return 'redis'
    if backend in {'database', 'local'}:
        return backend
    if backend in {'', 'auto'} and ergo == 'redis':
        return 'redis'
    if ergo == 'local':
        return 'local'
    return backend or 'unknown'


def _redis_section(project_root: Path) -> dict[str, Any]:
    path = project_root / 'databases.yaml'
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except Exception:  # noqa: BLE001
        return {}
    section = data.get('redis')
    return dict(section) if isinstance(section, dict) else {}


def _redis_connect_kwargs(project_root: Path, environ: dict[str, str]) -> dict[str, Any]:
    section = _redis_section(project_root)
    explicit = (environ.get('CELERY_BROKER_URL') or '').strip()
    if explicit:
        parts = urlsplit(explicit)
        db = 0
        if parts.path and parts.path != '/':
            try:
                db = int(parts.path.strip('/').split('/')[0])
            except ValueError:
                db = REDIS_CELERY_BROKER_DB_DEFAULT
        return {
            'host': parts.hostname or '127.0.0.1',
            'port': parts.port or 6379,
            'db': db,
            'username': parts.username,
            'password': parts.password,
            'socket_timeout': 1.5,
            'socket_connect_timeout': 1.5,
        }

    host = str(
        os.environ.get('REDIS_HOST')
        or section.get('host')
        or environ.get('REDIS_HOST')
        or '127.0.0.1'
    ).strip()
    # Имя сервиса compose имеет смысл только внутри контейнера.
    if not host:
        host = '127.0.0.1'
    elif host.lower() == 'redis' and not Path('/.dockerenv').is_file():
        host = '127.0.0.1'
    try:
        port = int(section.get('port') or environ.get('REDIS_PORT') or 6379)
    except (TypeError, ValueError):
        port = 6379
    try:
        db = int(
            section.get('db_celery_broker')
            or environ.get('REDIS_DB_CELERY_BROKER')
            or REDIS_CELERY_BROKER_DB_DEFAULT
        )
    except (TypeError, ValueError):
        db = REDIS_CELERY_BROKER_DB_DEFAULT
    password = str(section.get('password') or '').strip() or None
    username = str(section.get('user') or '').strip() or None
    return {
        'host': host,
        'port': port,
        'db': db,
        'username': username,
        'password': password,
        'socket_timeout': 1.5,
        'socket_connect_timeout': 1.5,
    }


def _llen(client: Any, queue: str) -> int | None:
    keys = (queue, f'{{{queue}}}', f'{queue}.kombu')
    total = 0
    seen = False
    for key in keys:
        try:
            value = client.llen(key)
        except Exception:  # noqa: BLE001
            continue
        if value is None:
            continue
        seen = True
        total += int(value)
    return total if seen else 0


def observe_queues(project_root: Path, extra_names: list[str] | None = None) -> QueuesReport:
    environ = load_project_env(project_root)
    names = load_queue_names(project_root)
    if extra_names:
        names = sorted(set(names) | {item for item in extra_names if item})
    kind = _broker_kind(environ)
    if kind != 'redis':
        snapshots = tuple(
            QueueSnapshot(name=name, depth=None, source=kind) for name in names
        )
        return QueuesReport(broker=kind, queues=snapshots, known=False)

    try:
        import redis as redis_lib
    except ImportError:
        snapshots = tuple(
            QueueSnapshot(name=name, depth=None, source='redis-missing') for name in names
        )
        return QueuesReport(broker='redis', queues=snapshots, known=False)

    kwargs = _redis_connect_kwargs(project_root, environ)
    try:
        client = redis_lib.Redis(**kwargs)
        client.ping()
    except Exception:  # noqa: BLE001 — брокер недоступен → unknown, CLI жив
        snapshots = tuple(
            QueueSnapshot(name=name, depth=None, source='redis-unavailable')
            for name in names
        )
        return QueuesReport(broker='redis', queues=snapshots, known=False)

    snapshots = tuple(
        QueueSnapshot(name=name, depth=_llen(client, name), source='redis')
        for name in names
    )
    return QueuesReport(broker='redis', queues=snapshots, known=True)


def redis_url_for_log(project_root: Path) -> str:
    """URL без пароля — только для отчёта."""
    environ = load_project_env(project_root)
    kwargs = _redis_connect_kwargs(project_root, environ)
    host = kwargs.get('host') or '127.0.0.1'
    port = kwargs.get('port') or 6379
    db = kwargs.get('db') or 0
    password = kwargs.get('password')
    if password:
        return f'redis://:{quote("***", safe="")}@{host}:{port}/{db}'
    return f'redis://{host}:{port}/{db}'
