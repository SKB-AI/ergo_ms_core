"""Обёртка: вызов Django-команды loadtest_provision_users из CLI loadtest."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


def _venv_python(root: Path) -> Path:
    win = root / 'virtual_env' / 'python' / 'Scripts' / 'python.exe'
    if win.is_file():
        return win
    unix = root / 'virtual_env' / 'python' / 'bin' / 'python'
    if unix.is_file():
        return unix
    return Path(sys.executable)


def _run_api_command(
    root: Path,
    *args: str,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    python = _venv_python(root)
    api_cwd = root / 'core' / 'api'
    run_env = os.environ.copy()
    if env:
        run_env.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        [str(python), '-m', 'commands', *args],
        cwd=str(api_cwd),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
        env=run_env,
    )


def _read_payload(out_path: Path) -> dict[str, Any]:
    try:
        return json.loads(out_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'не удалось прочитать {out_path}: {exc}') from exc


def provision_users(
    root: Path,
    *,
    count: int,
    out_path: Path,
    run_id: str | None = None,
    start_index: int = 1,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Создать count пользователей начиная с start_index; вернуть payload."""
    cmd = [
        'loadtest_provision_users',
        '--count',
        str(count),
        '--start-index',
        str(start_index),
        '--out',
        str(out_path),
    ]
    if run_id:
        cmd.extend(['--run-id', run_id])
    result = _run_api_command(root, *cmd, env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(detail or f'exit {result.returncode}')
    return _read_payload(out_path)


def ensure_users(
    root: Path,
    *,
    total: int,
    out_path: Path,
    run_id: str | None,
    access_tokens: list[str],
    env: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """
    Довести число JWT до total в рамках одного run_id.

    Возвращает (run_id, полный список access_tokens).
    """
    if total < 1:
        raise RuntimeError('total must be >= 1')

    have = len(access_tokens)
    if have >= total:
        if not run_id:
            raise RuntimeError('run_id required when tokens already present')
        return run_id, access_tokens

    need = total - have
    start_index = have + 1
    payload = provision_users(
        root,
        count=need,
        out_path=out_path,
        run_id=run_id,
        start_index=start_index,
        env=env,
    )
    new_run_id = str(payload.get('run_id') or run_id or '')
    if not new_run_id:
        raise RuntimeError('provision returned empty run_id')
    new_tokens = [
        str(tok) for tok in (payload.get('access_tokens') or []) if tok
    ]
    if len(new_tokens) != need:
        raise RuntimeError(
            f'expected {need} new tokens, got {len(new_tokens)}'
        )
    return new_run_id, access_tokens + new_tokens


def cleanup_users(
    root: Path,
    *,
    run_id: str | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Удалить пользователей прогона run_id или всех lt_* (если run_id пуст)."""
    cmd = ['loadtest_provision_users', '--cleanup']
    if run_id:
        cmd.extend(['--run-id', run_id])
    # stdout наследуем — прогресс «Удалено: N/M» виден в терминале.
    python = _venv_python(root)
    api_cwd = root / 'core' / 'api'
    run_env = os.environ.copy()
    if env:
        run_env.update({k: str(v) for k, v in env.items()})
    run_env.setdefault('PYTHONUNBUFFERED', '1')
    result = subprocess.run(
        [str(python), '-m', 'commands', *cmd],
        cwd=str(api_cwd),
        stdout=None,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
        env=run_env,
    )
    if result.returncode != 0:
        detail = (result.stderr or '').strip()
        raise RuntimeError(detail or f'exit {result.returncode}')
