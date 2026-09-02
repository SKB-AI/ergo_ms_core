"""
Ротация логов nginx, Redis, client-dev, LLM serve, Jupyter, Meilisearch, portable PostgreSQL и CLI ergoms по размеру.

nginx: переименование + nginx -s reopen (открывает новые файлы по конфигу).
Остальные: copytruncate (процесс держит тот же дескриптор).
"""

from __future__ import annotations

import argparse
import os
import shutil
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
from log_env import (  # noqa: E402
    infra_rotation_settings,
    log_file_path,
    nginx_access_log_enabled,
    resolve_logs_dir,
)


def _backup_path(path: Path, index: int) -> Path:
    return Path(f'{path}.{index}')


def _shift_backups(path: Path, backup_count: int) -> None:
    oldest = _backup_path(path, backup_count)
    if oldest.is_file():
        oldest.unlink()
    for index in range(backup_count - 1, 0, -1):
        src = _backup_path(path, index)
        if src.is_file():
            src.rename(_backup_path(path, index + 1))


def rotate_rename(path: Path, max_bytes: int, backup_count: int) -> bool:
    if not path.is_file() or path.stat().st_size <= max_bytes:
        return False
    _shift_backups(path, backup_count)
    path.rename(_backup_path(path, 1))
    return True


def rotate_copytruncate(path: Path, max_bytes: int, backup_count: int) -> bool:
    if not path.is_file() or path.stat().st_size <= max_bytes:
        return False
    _shift_backups(path, backup_count)
    shutil.copy2(path, _backup_path(path, 1))
    with path.open('w', encoding='utf-8'):
        pass
    return True


def nginx_paths(root: Path) -> tuple[Path, Path, Path]:
    nginx_dir = root / 'virtual_env' / 'packages' / 'nginx'
    if os.name == 'nt':
        exe = nginx_dir / 'nginx.exe'
    else:
        exe = nginx_dir / 'sbin' / 'nginx'
    main_conf = nginx_dir / 'conf' / 'nginx.conf'
    return nginx_dir, exe, main_conf


def reopen_nginx_logs(root: Path) -> bool:
    nginx_dir, exe, main_conf = nginx_paths(root)
    if not exe.is_file() or not main_conf.is_file():
        return False

    pid_file = nginx_dir / 'logs' / 'nginx.pid'
    running = False
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding='utf-8').strip())
            if os.name == 'nt':
                result = subprocess.run(
                    ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    check=False,
                )
                running = str(pid) in (result.stdout or '')
            else:
                os.kill(pid, 0)
                running = True
        except (OSError, ValueError, ProcessLookupError):
            running = False

    if not running:
        return False

    test = subprocess.run(
        [str(exe), '-t', '-c', str(main_conf)],
        cwd=str(nginx_dir),
        capture_output=True,
        check=False,
    )
    if test.returncode != 0:
        return False

    reopen = subprocess.run(
        [str(exe), '-s', 'reopen', '-c', str(main_conf)],
        cwd=str(nginx_dir),
        capture_output=True,
        check=False,
    )
    return reopen.returncode == 0


def rotate_infra_logs(root: Path, *, dry_run: bool = False, verbose: bool = False) -> int:
    settings = infra_rotation_settings(root)
    if not settings['enabled']:
        if verbose:
            print('[ergoms] Infra log rotation disabled (ERGO_LOG_INFRA_ROTATE_ENABLED=false)')
        return 0

    rotated_any = False
    nginx_rotated = False

    targets: list[tuple[str, Path, int, int, str]] = [
        (
            'nginx-error',
            log_file_path('NGINX_ERROR', root),
            int(settings['nginx_max_bytes']),
            int(settings['nginx_backup_count']),
            'rename',
        ),
        (
            'redis',
            log_file_path('REDIS', root),
            int(settings['redis_max_bytes']),
            int(settings['redis_backup_count']),
            'copytruncate',
        ),
        (
            'client-dev',
            log_file_path('CLIENT_DEV', root),
            int(settings['client_dev_max_bytes']),
            int(settings['client_dev_backup_count']),
            'copytruncate',
        ),
        (
            'ollama',
            log_file_path('OLLAMA', root),
            int(settings['copytruncate_max_bytes']),
            int(settings['copytruncate_backup_count']),
            'copytruncate',
        ),
        (
            'jupyter',
            log_file_path('JUPYTER', root),
            int(settings['copytruncate_max_bytes']),
            int(settings['copytruncate_backup_count']),
            'copytruncate',
        ),
        (
            'meilisearch',
            log_file_path('MEILISEARCH', root),
            int(settings['copytruncate_max_bytes']),
            int(settings['copytruncate_backup_count']),
            'copytruncate',
        ),
        (
            'postgres',
            log_file_path('POSTGRES', root),
            int(settings['copytruncate_max_bytes']),
            int(settings['copytruncate_backup_count']),
            'copytruncate',
        ),
        (
            'setup-full',
            log_file_path('SETUP_FULL', root),
            int(settings['copytruncate_max_bytes']),
            int(settings['copytruncate_backup_count']),
            'copytruncate',
        ),
        (
            'ergoms',
            log_file_path('ERGOMS', root),
            int(settings['copytruncate_max_bytes']),
            int(settings['copytruncate_backup_count']),
            'copytruncate',
        ),
    ]

    if nginx_access_log_enabled(root):
        targets.insert(
            1,
            (
                'nginx-access',
                log_file_path('NGINX_ACCESS', root),
                int(settings['nginx_max_bytes']),
                int(settings['nginx_backup_count']),
                'rename',
            ),
        )

    resolve_logs_dir(root).mkdir(parents=True, exist_ok=True)

    for label, path, max_bytes, backup_count, mode in targets:
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size <= max_bytes:
            if verbose:
                print(t('log_rotate_skip_size', label=label, path=path, size=size, max_bytes=max_bytes))
            continue

        if dry_run:
            print(t('log_rotate_dry_run', label=label, path=path, size=size, max_bytes=max_bytes))
            rotated_any = True
            if mode == 'rename':
                nginx_rotated = True
            continue

        if mode == 'rename':
            did = rotate_rename(path, max_bytes, backup_count)
            if did:
                print(t('log_rotate_ok', label=label, path=path))
                rotated_any = True
                nginx_rotated = True
        else:
            did = rotate_copytruncate(path, max_bytes, backup_count)
            if did:
                print(t('log_rotate_ok', label=label, path=path))
                rotated_any = True

    if nginx_rotated and not dry_run:
        if reopen_nginx_logs(root):
            print(t('log_rotate_nginx_reopened'))
        else:
            print(t('log_rotate_nginx_reopen_skipped'))

    if verbose and not rotated_any:
        print(t('log_rotate_not_needed'))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Rotate nginx/redis/client-dev logs by size (.env)')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT, help='Project root')
    parser.add_argument('--dry-run', action='store_true', help='Only show what would rotate')
    parser.add_argument('-v', '--verbose', action='store_true', help='Print skipped files')
    args = parser.parse_args()
    return rotate_infra_logs(args.root.resolve(), dry_run=args.dry_run, verbose=args.verbose)


if __name__ == '__main__':
    raise SystemExit(main())
