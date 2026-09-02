"""
Shared Meilisearch status / test for host lifecycle.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _DEPLOYMENT_DIR / 'scripts'
for _path in (_DEPLOYMENT_DIR, _SCRIPTS_DIR):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from install_meilisearch import (  # noqa: E402
    is_installed,
    meilisearch_data_dir,
    meilisearch_packages_dir,
    ping_meilisearch,
)

MEILISEARCH_WINDOWS_SERVICE = 'ergo_ms_meilisearch'
MEILISEARCH_LINUX_SERVICE = 'ergo_ms_meilisearch.service'


def _is_meilisearch_service_active() -> bool:
    if os.name == 'nt':
        result = subprocess.run(
            ['sc', 'query', MEILISEARCH_WINDOWS_SERVICE],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
        if result.returncode != 0:
            return False
        return 'RUNNING' in (result.stdout or '').upper()

    active = subprocess.run(
        ['systemctl', 'is-active', '--quiet', MEILISEARCH_LINUX_SERVICE],
        check=False,
    )
    return active.returncode == 0


def cmd_test(root: Path) -> int:
    if not is_installed(root):
        print(format_console('error', t('meilisearch_not_installed')), file=sys.stderr)
        return 1
    if ping_meilisearch(root):
        print(format_console('ok', 'OK'))
        return 0
    print(format_console('error', t('meilisearch_health_failed')), file=sys.stderr)
    return 1


def cmd_status(root: Path) -> int:
    if not is_installed(root):
        print(t('meilisearch_status_not_installed'))
        print(t('expected_path', path=meilisearch_packages_dir(root)))
        return 0

    print('')
    print(t('meilisearch_status_heading'))
    if _is_meilisearch_service_active():
        print(t('meilisearch_status_service_running'))
    elif ping_meilisearch(root):
        print(t('meilisearch_status_running'))
    else:
        print(t('meilisearch_status_stopped'))
    print(f'  Data: {meilisearch_data_dir(root)}')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Meilisearch backend (status/test)')
    parser.add_argument('operation', choices=('status', 'test'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[5])
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if args.operation == 'status':
        return cmd_status(root)
    return cmd_test(root)


if __name__ == '__main__':
    raise SystemExit(main())
