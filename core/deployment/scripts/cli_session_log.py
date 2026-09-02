"""Подготовка файлового журнала сессии ergoms (stdlib, до venv).

setup / setup-full → logs/setup-full.log
остальные конечные команды → logs/ergoms.log
Имена — LOG_FILE_DEFAULTS / ERGO_LOG_FILE_*.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = SCRIPTS_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from deployment_env import PROJECT_ROOT  # noqa: E402
from log_env import log_file_path, rotation_settings  # noqa: E402

CLI_LOG_ATTACHED_ENV = 'ERGO_CLI_LOG_ATTACHED'

_SETUP_COMMANDS = frozenset({'setup', 'setup-full', 'setup_full'})

# Долгие процессы уже пишут в свои файлы (api.log, client-dev.log, …).
_SKIP_COMMANDS = frozenset({
    'logs',
    'help',
    'dev',
    'start-client',
    'start-client-dev',
    'start-worker',
    'start-beat',
    'start-jupyter',
    'start-jupyter-dev',
    'start-api',
    'start-media',
    'start-module',
    'start-all',
    'start-meilisearch-dev',
    'start-db-dev',
    'start-redis-dev',
    'start-nginx-dev',
    'docker-dev',
    'docker-prod',
    'docker-up',
    'docker-logs',
    'loadtest',
    'poetry',
    'api',
    'npm',
    'media_api',
})


def normalize_command(command: str) -> str:
    return (command or '').strip().replace('_', '-')


def log_key_for_command(command: str) -> str | None:
    """Ключ LOG_FILE_DEFAULTS или None, если сессию в файл не пишем."""
    cmd = normalize_command(command)
    if not cmd or cmd in _SKIP_COMMANDS:
        return None
    if cmd in _SETUP_COMMANDS:
        return 'SETUP_FULL'
    module_name, sep, rest = cmd.partition(':')
    if sep and module_name and rest:
        if rest == 'start' or (rest.startswith('start-') and 'service' not in rest):
            return None
    return 'ERGOMS'


def already_attached() -> bool:
    return os.environ.get(CLI_LOG_ATTACHED_ENV, '').strip() in ('1', 'true', 'yes', 'on')


def _rotate_if_needed(path: Path, max_bytes: int, backup_count: int) -> None:
    if not path.is_file() or path.stat().st_size <= max_bytes:
        return
    if backup_count < 1:
        path.write_text('', encoding='utf-8')
        return
    oldest = Path(f'{path}.{backup_count}')
    if oldest.is_file():
        oldest.unlink()
    for index in range(backup_count - 1, 0, -1):
        src = Path(f'{path}.{index}')
        if src.is_file():
            src.rename(Path(f'{path}.{index + 1}'))
    path.rename(Path(f'{path}.1'))


def prepare_session_log(command: str, project_root: Path | None = None) -> Path | None:
    key = log_key_for_command(command)
    if key is None:
        return None
    root = project_root or PROJECT_ROOT
    path = log_file_path(key, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    rot = rotation_settings(root)
    _rotate_if_needed(path, int(rot['max_bytes']), int(rot['backup_count']))
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    header = (
        f'\n----------\n'
        f'[INFO] {stamp} ergoms {normalize_command(command)}\n'
        f'cwd={Path.cwd()}\n'
        f'----------\n'
    )
    try:
        with path.open('a', encoding='utf-8') as handle:
            handle.write(header)
    except OSError:
        # Журнал занят (Start-Transcript, другой процесс). Сессия идёт только в консоль.
        return None
    return path


def _cli_main() -> int:
    if len(sys.argv) < 3:
        print('использование: cli_session_log.py key|prepare <command> [root]', file=sys.stderr)
        return 1
    action = sys.argv[1]
    command = sys.argv[2]
    root = Path(sys.argv[3]) if len(sys.argv) >= 4 else PROJECT_ROOT
    if action == 'key':
        key = log_key_for_command(command)
        if key:
            print(key, end='')
        return 0
    if action == 'prepare':
        if already_attached():
            return 2
        path = prepare_session_log(command, root)
        if path is None:
            return 2
        print(path, end='')
        return 0
    print(f'Неизвестная команда: {action}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(_cli_main())
