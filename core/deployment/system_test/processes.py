"""Запуск и остановка процессов API/клиента в изолированном дереве."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping

from loadtest.ephemeral_api import (
    EphemeralApi,
    start_ephemeral_api,
    stop_ephemeral_api,
)

from .environment import venv_python
from .http import wait_http


def start_api(root: Path, *, yaml_path: Path, port: int, extra_env: Mapping[str, str] | None = None) -> EphemeralApi:
    merged = {
        'NO_PROXY': '127.0.0.1,localhost',
        'no_proxy': '127.0.0.1,localhost',
        'ERGO_LOG_CONSOLE': 'false',
        'API_AUTORELOAD': 'false',
    }
    if extra_env:
        merged.update(dict(extra_env))
    api = start_ephemeral_api(root, yaml_path=yaml_path, port=port, extra_env=merged)
    try:
        wait_http(api.base_url, timeout_sec=420.0, path='/api/')
    except Exception:
        stop_api(api)
        raise
    return api


def stop_api(api: EphemeralApi | None) -> None:
    if api is None:
        return
    if os.name == 'nt' and api.process.pid:
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(api.process.pid)],
            capture_output=True,
            check=False,
        )
    stop_ephemeral_api(api)


def start_client(
    root: Path,
    *,
    client_url: str,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.Popen[str]:
    python = venv_python(root)
    script = root / 'core' / 'deployment' / 'scripts' / 'start_client_if_dev.py'
    if not python.is_file() or not script.is_file():
        raise RuntimeError('нет python или start_client_if_dev.py в изолированном дереве')
    env = os.environ.copy()
    if extra_env:
        env.update({key: str(value) for key, value in extra_env.items()})
    proc = subprocess.Popen(
        [str(python), str(script)],
        cwd=str(root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        wait_http(client_url, timeout_sec=180.0, path='/')
    except Exception:
        stop_proc(proc)
        raise
    return proc


def stop_proc(proc: subprocess.Popen[str] | None, *, grace_sec: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3.0)
