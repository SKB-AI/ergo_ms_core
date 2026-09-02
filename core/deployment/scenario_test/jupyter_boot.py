"""Ставит optional-группу jupyter в контейнере и запускает JupyterLab.

Образ ergo_ms-python:local собирается с poetry --only main, поэтому JupyterLab
в API-слое нет. Живой сценарий ставит группу в overlay этого контейнера
(хостовый venv и корневой .env не трогает), затем вызывает start_jupyter.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_API_DIR = Path('/app/core/api')
_START = _API_DIR / 'scripts' / 'start_jupyter.py'


def main() -> int:
    install = subprocess.run(
        [sys.executable, '-m', 'commands', 'install', '--with', 'jupyter'],
        cwd=str(_API_DIR),
        check=False,
    )
    if install.returncode != 0:
        return int(install.returncode)
    return int(subprocess.call([sys.executable, str(_START)]))


if __name__ == '__main__':
    raise SystemExit(main())
