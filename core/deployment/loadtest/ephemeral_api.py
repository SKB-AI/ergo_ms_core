"""
Временный API для --isolated-db (отдельный порт + ERGO_DATABASES_YAML).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass
class EphemeralApi:
    process: subprocess.Popen[str]
    host: str
    port: int
    yaml_path: Path

    @property
    def base_url(self) -> str:
        return f'http://{self.host}:{self.port}'


def _venv_python(root: Path) -> Path:
    win = root / 'virtual_env' / 'python' / 'Scripts' / 'python.exe'
    if win.is_file():
        return win
    unix = root / 'virtual_env' / 'python' / 'bin' / 'python'
    if unix.is_file():
        return unix
    return Path(sys.executable)


def start_ephemeral_api(
    root: Path,
    *,
    yaml_path: Path,
    port: int,
    bind_host: str = '127.0.0.1',
    extra_env: Mapping[str, str] | None = None,
) -> EphemeralApi:
    python = _venv_python(root)
    script = root / 'core' / 'api' / 'scripts' / 'start_api.py'
    if not script.is_file():
        raise RuntimeError(f'start_api.py not found: {script}')

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env['ERGO_DATABASES_YAML'] = str(yaml_path.resolve())
    env['API_PORT'] = str(port)
    env['API_HOST'] = bind_host
    # Не мешать основному API / nginx на 8000.
    path_entries = [str(root), str(root / 'core' / 'api')]
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = os.pathsep.join(
        path_entries + ([existing] if existing else [])
    )
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})

    proc = subprocess.Popen(
        [str(python), str(script)],
        cwd=str(root / 'core' / 'api'),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return EphemeralApi(process=proc, host=bind_host, port=port, yaml_path=yaml_path)


def wait_api_ready(
    base_url: str,
    *,
    timeout_sec: float = 90.0,
    process: subprocess.Popen[str] | None = None,
) -> None:
    """Ждать ответ HTTP (любой код кроме connect error)."""
    deadline = time.monotonic() + timeout_sec
    url = base_url.rstrip('/') + '/api/'
    last_err = ''
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f'ephemeral API exited early (code={process.returncode})'
            )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=2.0) as resp:
                _ = resp.status
            return
        except urllib.error.HTTPError:
            # API поднялся, даже если 401/404
            return
        except Exception as exc:  # noqa: BLE001 — wait loop
            last_err = str(exc)
            time.sleep(0.5)
    raise RuntimeError(
        f'ephemeral API not ready within {timeout_sec:.0f}s at {url}: {last_err}'
    )


def stop_ephemeral_api(api: EphemeralApi, *, grace_sec: float = 5.0) -> None:
    proc = api.process
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3.0)
