"""
Установка автоматической ротации infra-логов (nginx, redis, client-dev, LLM, CLI).

Linux: cron (каждый час или ежедневно в ERGO_LOG_INFRA_ROTATE_HOUR).
Windows: задача планировщика «ERGO MS Log Rotate».
Параметры — в .env (ERGO_LOG_INFRA_*, ERGO_LOG_COMPRESS); выполняет rotate_infra_logs.py.
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
from deployment_env import PROJECT_ROOT  # noqa: E402
from log_env import infra_rotation_settings  # noqa: E402

CRON_MARKER = '# ergo_ms infra log rotate'
TASK_NAME = 'ERGO MS Log Rotate'


def _python_exe(root: Path) -> Path:
    if os.name == 'nt':
        return root / 'virtual_env' / 'python' / 'Scripts' / 'python.exe'
    return root / 'virtual_env' / 'python' / 'bin' / 'python'


def _rotate_command(root: Path) -> str:
    py = _python_exe(root)
    script = root / 'core' / 'deployment' / 'scripts' / 'rotate_infra_logs.py'
    if os.name == 'nt':
        return f'"{py}" "{script}" --root "{root}"'
    return f'{py} {script} --root {root}'


def _linux_cron_line(root: Path, hour: int, interval: str) -> str:
    schedule = '0 * * * *' if interval == 'hourly' else f'0 {hour} * * *'
    return f'{schedule} {_rotate_command(root)} >> {root / "logs" / "rotate-infra.log"} 2>&1 {CRON_MARKER}'


def install_linux(root: Path, hour: int, interval: str, *, uninstall: bool) -> int:
    cron_line = _linux_cron_line(root, hour, interval)
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, check=False)
        existing = result.stdout if result.returncode == 0 else ''
    except OSError as exc:
        print(f'[ERROR] crontab unavailable: {exc}', file=sys.stderr)
        return 1

    lines = [line for line in existing.splitlines() if CRON_MARKER not in line]
    if uninstall:
        new_crontab = '\n'.join(lines).strip()
        if new_crontab:
            new_crontab += '\n'
        proc = subprocess.run(['crontab', '-'], input=new_crontab, text=True, check=False)
        if proc.returncode == 0:
            print(t('log_rotate_cron_removed'))
        return proc.returncode

    lines.append(cron_line)
    new_crontab = '\n'.join(lines).strip() + '\n'
    proc = subprocess.run(['crontab', '-'], input=new_crontab, text=True, check=False)
    if proc.returncode != 0:
        print(t('log_rotate_crontab_failed'), file=sys.stderr)
        return proc.returncode
    if interval == 'hourly':
        print(t('log_rotate_cron_scheduled_hourly'))
    else:
        print(t('log_rotate_cron_scheduled', hour=f'{hour:02d}'))
    print(t('log_rotate_log_path', path=root / 'logs' / 'rotate-infra.log'))
    return 0


def install_windows(root: Path, hour: int, interval: str, *, uninstall: bool) -> int:
    cmd = _rotate_command(root)
    if uninstall:
        proc = subprocess.run(['schtasks', '/Delete', '/TN', TASK_NAME, '/F'], check=False)
        if proc.returncode == 0:
            print(t('log_rotate_task_removed', name=TASK_NAME))
        else:
            print(t('log_rotate_task_not_found'))
        return 0

    create_cmd = [
        'schtasks',
        '/Create',
        '/F',
        '/TN',
        TASK_NAME,
        '/TR',
        cmd,
        '/RL',
        'HIGHEST',
    ]
    if interval == 'hourly':
        create_cmd.extend(['/SC', 'HOURLY'])
    else:
        create_cmd.extend(['/SC', 'DAILY', '/ST', f'{hour:02d}:00'])
    proc = subprocess.run(
        create_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print('[ERROR] schtasks failed:', proc.stderr or proc.stdout, file=sys.stderr)
        print(t('admin_powershell_hint'), file=sys.stderr)
        return proc.returncode
    if interval == 'hourly':
        print(t('log_rotate_task_scheduled_hourly', name=TASK_NAME))
    else:
        print(t('log_rotate_task_scheduled', name=TASK_NAME, time=f'{hour:02d}:00'))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Install/uninstall scheduled infra log rotation')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()

    root = args.root.resolve()
    settings = infra_rotation_settings(root)
    hour = max(0, min(23, int(settings['schedule_hour'])))
    interval = str(settings.get('interval') or 'hourly')

    if not settings['enabled'] and not args.uninstall:
        print('[WARNING] ERGO_LOG_INFRA_ROTATE_ENABLED=false — installing schedule anyway (rotation no-op until enabled)')

    system = platform.system().lower()
    if system == 'windows':
        return install_windows(root, hour, interval, uninstall=args.uninstall)
    if system == 'linux':
        return install_linux(root, hour, interval, uninstall=args.uninstall)

    print(f'[ERROR] Unsupported OS for install-infra-log-rotate: {system}', file=sys.stderr)
    print(t('log_rotate_run_manually'), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
