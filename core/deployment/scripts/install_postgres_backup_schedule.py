"""
Ставит или снимает автоснимок SQL по POSTGRES_BACKUP_SCHEDULE.

Linux: crontab пользователя (при sudo — SUDO_USER).
Windows: задача планировщика «ERGO MS Postgres Backup».
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = SCRIPTS_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from db_backup_schedule import BackupSchedule, backup_schedule_time  # noqa: E402
from deployment_env import PROJECT_ROOT  # noqa: E402

CRON_MARKER = '# ergo_ms postgres backup'
TASK_NAME = 'ERGO MS Postgres Backup'


def _log(level: str, message: str) -> None:
    stream = sys.stderr if level == 'error' else sys.stdout
    print(format_console(level, message), file=stream, flush=True)


def _python_exe(root: Path) -> Path:
    if os.name == 'nt':
        return root / 'virtual_env' / 'python' / 'Scripts' / 'python.exe'
    return root / 'virtual_env' / 'python' / 'bin' / 'python'


def _backup_command(root: Path) -> str:
    py = _python_exe(root)
    script = root / 'core' / 'deployment' / 'scripts' / 'backup_database.py'
    if os.name == 'nt':
        return f'"{py}" "{script}" backup'
    return f'{py} {script} backup'


def _cron_user() -> str | None:
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        user = (os.environ.get('SUDO_USER') or '').strip()
        return user or None
    return None


def _crontab_cmd(*args: str) -> list[str]:
    user = _cron_user()
    if user:
        return ['crontab', '-u', user, *args]
    return ['crontab', *args]


def _read_crontab() -> str:
    result = subprocess.run(_crontab_cmd('-l'), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return ''
    return result.stdout or ''


def install_linux(root: Path, schedule: BackupSchedule, *, uninstall: bool) -> int:
    log_path = root / 'logs' / 'postgres-backup.log'
    cron_line = (
        f'{schedule.cron_expr()} {_backup_command(root)} >> {log_path} 2>&1 {CRON_MARKER}'
    )
    try:
        existing = _read_crontab()
    except OSError as exc:
        _log('error', t('db_backup_cron_unavailable', exc=str(exc)))
        return 1

    lines = [line for line in existing.splitlines() if CRON_MARKER not in line]
    if uninstall:
        new_crontab = '\n'.join(lines).strip()
        if new_crontab:
            new_crontab += '\n'
        proc = subprocess.run(_crontab_cmd('-'), input=new_crontab, text=True, check=False)
        if proc.returncode == 0:
            _log('ok', t('db_backup_cron_removed'))
        return proc.returncode

    lines.append(cron_line)
    new_crontab = '\n'.join(lines).strip() + '\n'
    proc = subprocess.run(_crontab_cmd('-'), input=new_crontab, text=True, check=False)
    if proc.returncode != 0:
        _log('error', t('db_backup_crontab_failed'))
        return proc.returncode
    _log('ok', t('db_backup_cron_scheduled', time=schedule.label()))
    _log('info', t('db_backup_schedule_log', path=str(log_path)))
    return 0


def install_windows(root: Path, schedule: BackupSchedule, *, uninstall: bool) -> int:
    if uninstall:
        proc = subprocess.run(['schtasks', '/Delete', '/TN', TASK_NAME, '/F'], check=False)
        if proc.returncode == 0:
            _log('ok', t('db_backup_task_removed', name=TASK_NAME))
        else:
            _log('info', t('db_backup_task_not_found', name=TASK_NAME))
        return 0

    proc = subprocess.run(
        [
            'schtasks',
            '/Create',
            '/F',
            *schedule.schtasks_args(),
            '/TN',
            TASK_NAME,
            '/TR',
            _backup_command(root),
            '/RL',
            'LIMITED',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _log('error', t('db_backup_task_failed', detail=(proc.stderr or proc.stdout or '').strip()))
        return proc.returncode
    _log('ok', t('db_backup_task_scheduled', name=TASK_NAME, time=schedule.label()))
    return 0


def apply_schedule(root: Path, *, uninstall: bool = False) -> int:
    configure_stdio_utf8()
    root = root.resolve()
    schedule = backup_schedule_time()
    disable = uninstall or schedule is None
    system = platform.system().lower()
    fallback = BackupSchedule(kind='daily', hour=3, minute=0)
    if system == 'windows':
        code = install_windows(root, schedule or fallback, uninstall=disable)
    elif system == 'linux':
        code = install_linux(root, schedule or fallback, uninstall=disable)
    else:
        _log('error', t('db_backup_schedule_os_unsupported', system=system))
        return 1
    if code == 0 and schedule is None and not uninstall:
        _log('skip', t('db_backup_schedule_disabled'))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=t('db_backup_schedule_cli'))
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args(argv)
    return apply_schedule(args.root, uninstall=bool(args.uninstall))


if __name__ == '__main__':
    raise SystemExit(main())
