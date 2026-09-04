"""
Ротация и гигиена логов nginx, Redis, client-dev, LLM serve, Jupyter, Meilisearch, portable PostgreSQL и CLI ergoms.

nginx: переименование + nginx -s reopen (открывает новые файлы по конфигу).
Остальные: copytruncate (процесс держит тот же дескриптор).
Копии сжимаются gzip; срок хранения — ERGO_LOG_RETENTION_DAYS.
"""

from __future__ import annotations

import argparse
import json
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
from log_hygiene import (  # noqa: E402
    backup_plain,
    compress_numbered_backups,
    format_bytes,
    list_log_files,
    prune_numbered_backups,
    shift_backups,
)


def rotate_rename(path: Path, max_bytes: int, backup_count: int) -> bool:
    if not path.is_file() or path.stat().st_size <= max_bytes:
        return False
    shift_backups(path, backup_count)
    path.rename(backup_plain(path, 1))
    return True


def rotate_copytruncate(path: Path, max_bytes: int, backup_count: int) -> bool:
    if not path.is_file() or path.stat().st_size <= max_bytes:
        return False
    shift_backups(path, backup_count)
    shutil.copy2(path, backup_plain(path, 1))
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


def _infra_targets(root: Path, settings: dict[str, object]) -> list[tuple[str, Path, int, int, str]]:
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
    return targets


def print_logs_status(root: Path, *, as_json: bool = False) -> int:
    logs_dir = resolve_logs_dir(root)
    files = list_log_files(logs_dir)
    total = sum(item.size for item in files)
    if as_json:
        payload = {
            'dir': str(logs_dir),
            'total_bytes': total,
            'files': [
                {
                    'path': str(item.path.relative_to(logs_dir)),
                    'bytes': item.size,
                }
                for item in files
            ],
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if not files:
        print(t('log_status_empty', path=logs_dir))
        return 0
    print(t('log_status_header', path=logs_dir))
    for item in files:
        rel = item.path.relative_to(logs_dir)
        print(f'{format_bytes(item.size):>8}  {rel}')
    print(t('log_status_total', size=format_bytes(total), count=len(files)))
    return 0


def _apply_hygiene(
    label: str,
    path: Path,
    backup_count: int,
    *,
    compress: bool,
    retention_days: int,
    dry_run: bool,
) -> bool:
    changed = False
    if compress:
        if dry_run:
            for index in range(1, backup_count + 1):
                raw = backup_plain(path, index)
                if raw.is_file():
                    print(t('log_rotate_compress_dry', label=label, path=raw, size=raw.stat().st_size))
                    changed = True
        else:
            for dest, before, after in compress_numbered_backups(path, backup_count):
                print(
                    t(
                        'log_rotate_compressed',
                        label=label,
                        path=dest,
                        before=format_bytes(before),
                        after=format_bytes(after),
                    )
                )
                changed = True
    if dry_run:
        return changed
    for removed in prune_numbered_backups(path, backup_count, retention_days):
        print(t('log_rotate_pruned', label=label, path=removed))
        changed = True
    return changed


def rotate_infra_logs(root: Path, *, dry_run: bool = False, verbose: bool = False) -> int:
    settings = infra_rotation_settings(root)
    if not settings['enabled']:
        if verbose:
            print('[ergoms] Infra log rotation disabled (ERGO_LOG_INFRA_ROTATE_ENABLED=false)')
        return 0

    rotated_any = False
    nginx_rotated = False
    targets = _infra_targets(root, settings)
    compress = bool(settings['compress'])
    retention_days = int(settings['retention_days'])
    resolve_logs_dir(root).mkdir(parents=True, exist_ok=True)

    for label, path, max_bytes, backup_count, mode in targets:
        if path.is_file():
            size = path.stat().st_size
            if size > max_bytes:
                if dry_run:
                    print(t('log_rotate_dry_run', label=label, path=path, size=size, max_bytes=max_bytes))
                    rotated_any = True
                    if mode == 'rename':
                        nginx_rotated = True
                elif mode == 'rename':
                    if rotate_rename(path, max_bytes, backup_count):
                        print(t('log_rotate_ok', label=label, path=path))
                        rotated_any = True
                        nginx_rotated = True
                elif rotate_copytruncate(path, max_bytes, backup_count):
                    print(t('log_rotate_ok', label=label, path=path))
                    rotated_any = True
            elif verbose:
                print(t('log_rotate_skip_size', label=label, path=path, size=size, max_bytes=max_bytes))
        if _apply_hygiene(
            label,
            path,
            backup_count,
            compress=compress,
            retention_days=retention_days,
            dry_run=dry_run,
        ):
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
    parser = argparse.ArgumentParser(description='Rotate and compress infra logs by size (.env)')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT, help='Project root')
    parser.add_argument('--dry-run', action='store_true', help='Only show what would rotate')
    parser.add_argument('-v', '--verbose', action='store_true', help='Print skipped files')
    parser.add_argument('--status', action='store_true', help='Show logs/ disk usage and exit')
    parser.add_argument('--json', action='store_true', help='JSON for --status')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.status:
        return print_logs_status(root, as_json=args.json)
    return rotate_infra_logs(root, dry_run=args.dry_run, verbose=args.verbose)


if __name__ == '__main__':
    raise SystemExit(main())
