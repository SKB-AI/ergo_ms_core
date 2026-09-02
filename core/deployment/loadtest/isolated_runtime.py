"""
Контекст изоляции БД для loadtest: host --isolated-db или --docker-isolated.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from loadtest.ephemeral_api import (  # noqa: E402
    EphemeralApi,
    start_ephemeral_api,
    stop_ephemeral_api,
    wait_api_ready,
)
from loadtest.isolated_db import (  # noqa: E402
    DEFAULT_LOADTEST_API_PORT,
    drop_clone,
    load_default_db,
    loadtest_env_for_yaml,
    refresh_clone,
    write_loadtest_databases_yaml,
)


@dataclass
class IsolatedSession:
    """Активная изоляция: host Locust + env для provision/cleanup."""

    mode: str  # host | docker
    base_url: str
    provision_env: dict[str, str]
    clone_name: str | None = None
    drop_db: bool = False
    api: EphemeralApi | None = None
    db_cfg: Any = None

    def close(self) -> None:
        if self.api is not None:
            stop_ephemeral_api(self.api)
            self.api = None
        if self.drop_db and self.db_cfg is not None and self.clone_name:
            drop_clone(self.db_cfg, clone_name=self.clone_name)


def start_host_isolated(
    root: Path,
    *,
    api_port: int = DEFAULT_LOADTEST_API_PORT,
    drop_db: bool = False,
) -> IsolatedSession:
    cfg = load_default_db(root)
    clone_name = refresh_clone(cfg)
    yaml_path = write_loadtest_databases_yaml(root, clone_name=clone_name)
    env = loadtest_env_for_yaml(yaml_path, api_port=api_port)
    api = start_ephemeral_api(root, yaml_path=yaml_path, port=api_port)
    try:
        wait_api_ready(api.base_url, process=api.process)
    except Exception:
        stop_ephemeral_api(api)
        if drop_db:
            drop_clone(cfg, clone_name=clone_name)
        raise
    return IsolatedSession(
        mode='host',
        base_url=api.base_url,
        provision_env=env,
        clone_name=clone_name,
        drop_db=drop_db,
        api=api,
        db_cfg=cfg,
    )


def start_docker_isolated(
    root: Path,
    *,
    api_port: int = DEFAULT_LOADTEST_API_PORT,
    api_host: str = '127.0.0.1',
    db_host: str = '127.0.0.1',
    db_port: int | None = None,
    clone_name: str | None = None,
) -> IsolatedSession:
    """
    Ждать api-loadtest; provision через yaml на опубликованный postgres-loadtest.
    """
    cfg = load_default_db(root)
    name = clone_name or f'{cfg.name}_loadtest'
    if db_port is not None:
        port = db_port
    else:
        try:
            port = int(os.environ.get('LOADTEST_POSTGRES_PORT') or 15432)
        except (TypeError, ValueError):
            port = 15432
    yaml_path = write_loadtest_databases_yaml(
        root,
        clone_name=name,
        host=db_host,
        port=port,
    )
    env = loadtest_env_for_yaml(yaml_path, api_port=api_port)
    base_url = f'http://{api_host}:{api_port}'
    try:
        wait_api_ready(base_url, timeout_sec=30.0)
    except RuntimeError as exc:
        raise RuntimeError(
            f'docker loadtest API not ready at {base_url}. '
            f'Start with DOCKER_PROFILE_LOADTEST=true (ergoms docker-up) '
            f'or ergoms docker-loadtest-up. Detail: {exc}'
        ) from exc
    return IsolatedSession(
        mode='docker',
        base_url=base_url,
        provision_env=env,
        clone_name=name,
        drop_db=False,
        api=None,
        db_cfg=None,
    )


def probe_url(url: str, *, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            _ = resp.status
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False
