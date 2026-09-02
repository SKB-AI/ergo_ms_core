"""Изолированная установка Jupyter: пакеты в каталоге прогона, не в virtual_env/python."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Как в pyproject.toml [tool.poetry.group.jupyter.dependencies]
_JUPYTER_REQUIREMENTS = (
    'ipykernel>=6.29.5',
    'notebook>=7.4.4,<8.0.0',
    'jupyterlab>=4.4.5,<5.0.0',
)


def python_overlay_dir(run_dir: Path) -> Path:
    return run_dir / 'python_overlay'


def jupyter_data_dir(run_dir: Path) -> Path:
    return run_dir / 'jupyter'


def jupyter_notebooks_dir(run_dir: Path) -> Path:
    return run_dir / 'notebooks'


def apply_python_overlay(env: dict[str, str], overlay: Path) -> None:
    overlay_s = str(overlay.resolve())
    existing = env.get('PYTHONPATH', '')
    parts = [overlay_s]
    for item in existing.split(os.pathsep):
        if item and item != overlay_s:
            parts.append(item)
    env['PYTHONPATH'] = os.pathsep.join(parts)
    env['PYTHONNOUSERSITE'] = '1'


def apply_jupyter_isolation(env: dict[str, str], run_dir: Path) -> None:
    data = jupyter_data_dir(run_dir)
    notebooks = jupyter_notebooks_dir(run_dir)
    data.mkdir(parents=True, exist_ok=True)
    notebooks.mkdir(parents=True, exist_ok=True)
    env['JUPYTER_DATA_DIR'] = str(data)
    env['JUPYTER_PATH'] = str(data)
    env['JUPYTER_RUNTIME_DIR'] = str(data / 'runtime')
    env['JUPYTER_NOTEBOOKS_DIR'] = str(notebooks)


def jupyterlab_importable(python: Path, overlay: Path) -> bool:
    result = subprocess.run(
        [
            str(python),
            '-c',
            'import sys; sys.path.insert(0, r"{path}"); import jupyterlab'.format(
                path=str(overlay.resolve())
            ),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def install_jupyter_overlay(
    *,
    python: Path,
    project_root: Path,
    overlay: Path,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    overlay.mkdir(parents=True, exist_ok=True)
    pip_cache = project_root / 'virtual_env' / 'cache' / 'pip'
    pip_cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['PIP_CACHE_DIR'] = str(pip_cache)
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy'):
        env.pop(key, None)
    env['NO_PROXY'] = '*'
    env['no_proxy'] = '*'
    return subprocess.run(
        [
            str(python),
            '-m',
            'pip',
            'install',
            '--target',
            str(overlay),
            '--disable-pip-version-check',
            '--no-warn-script-location',
            *_JUPYTER_REQUIREMENTS,
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        check=False,
    )
