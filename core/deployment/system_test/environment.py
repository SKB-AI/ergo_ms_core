"""Базовое изолированное окружение системного теста."""

from __future__ import annotations

import os
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Mapping


class IsolatedEnvironment(ABC):
    kind: str = ''

    def __init__(self, workspace: Path, run_dir: Path, prefix: str) -> None:
        self.workspace = workspace
        self.run_dir = run_dir
        self.prefix = prefix
        self.tree_root = run_dir / 'tree'

    @abstractmethod
    def provision(self) -> None:
        """Создать изолированное дерево и поставить систему с нуля."""

    @abstractmethod
    def start(self) -> None:
        """Запустить процессы или службы."""

    def run_ergoms(
        self,
        *args: str,
        timeout: int = 3600,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        root = self.tree_root if self.tree_root.is_dir() else self.workspace
        run_env = os.environ.copy()
        run_env['ERGO_SERVICE_PREFIX'] = self.prefix
        run_env['ERGOMS_PROJECT_ROOT'] = str(root)
        run_env['NO_PROXY'] = '127.0.0.1,localhost'
        run_env['no_proxy'] = '127.0.0.1,localhost'
        if env:
            run_env.update({key: str(value) for key, value in env.items()})
        if os.name == 'nt':
            script = root / 'core' / 'deployment' / 'windows' / 'ergo_ms.ps1'
            command = [
                'powershell',
                '-NoProfile',
                '-ExecutionPolicy',
                'Bypass',
                '-File',
                str(script),
                '-Root',
                str(root),
                *args,
            ]
        else:
            script = root / 'core' / 'deployment' / 'linux' / 'ergo_ms.sh'
            command = ['bash', str(script), *args]
        return subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=timeout,
            env=run_env,
        )

    def http_base(self) -> str:
        return 'http://127.0.0.1:8000'

    def client_base(self) -> str:
        return 'http://127.0.0.1:8001'

    def start_client(self) -> None:
        """Поднять клиент, если кейсу браузера нужен SPA. По умолчанию нет."""
        return

    def ensure_api(self) -> None:
        """Поднять API, если кейсу нужен HTTP. По умолчанию нет."""
        return

    @abstractmethod
    def teardown(self) -> None:
        """Остановить стек и снять тестовые службы. Вызывать в finally."""


def has_os_service_privilege() -> bool:
    if os.name == 'nt':
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, 'geteuid') and os.geteuid() == 0


def venv_python(root: Path) -> Path:
    if os.name == 'nt':
        return root / 'virtual_env' / 'python' / 'Scripts' / 'python.exe'
    return root / 'virtual_env' / 'python' / 'bin' / 'python'


def current_python() -> Path:
    return Path(sys.executable)
