"""
Потребление RAM/CPU процессов ERGO MS на хосте.

ergoms resource-usage [--json] [-w] [--interval N] [--root PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psutil

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402

from celery_balance.host_budget import collect_role_totals  # noqa: E402
from ergo_process_classifier import (  # noqa: E402
    PROJECT_ROOT,
    ProcessSample,
    iter_ergo_processes,
)


def resolve_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise SystemExit(format_console('error', t('project_dir_not_found', root=root)))
        return root
    if (PROJECT_ROOT / 'pyproject.toml').is_file():
        return PROJECT_ROOT
    raise SystemExit(format_console('error', t('project_root_resolve_failed')))


def collect_samples(root: Path) -> list[ProcessSample]:
    samples = list(iter_ergo_processes(root))
    samples.sort(key=lambda item: (item.role, item.pid))
    return samples


def build_payload(samples: list[ProcessSample]) -> dict[str, object]:
    total_memory_mb = round(sum(item.memory_mb for item in samples), 1)
    vm = psutil.virtual_memory()
    roles = collect_role_totals(samples)
    return {
        'processes': [
            {
                'role': item.role,
                'pid': item.pid,
                'name': item.name,
                'memory_mb': item.memory_mb,
                'cpu_percent': item.cpu_percent,
            }
            for item in samples
        ],
        'process_count': len(samples),
        'total_memory_mb': total_memory_mb,
        'host': {
            'ram_total_mb': round(vm.total / (1024 * 1024), 1),
            'ram_used_mb': round(vm.used / (1024 * 1024), 1),
            'ram_percent': round(float(vm.percent), 1),
            'cpu_count': int(psutil.cpu_count(logical=True) or 1),
        },
        'roles': {
            name: {
                'memory_mb': item.memory_mb,
                'cpu_percent': item.cpu_percent,
                'count': item.count,
            }
            for name, item in sorted(roles.items())
        },
    }


def print_table(samples: list[ProcessSample]) -> None:
    if not samples:
        print(format_console('info', t('no_ergo_processes')))
        return

    headers = (t('resource_header_role'), t('resource_header_pid'), t('resource_header_ram'), 'CPU %')
    rows = [
        (item.role, str(item.pid), f'{item.memory_mb:.1f}', f'{item.cpu_percent:.1f}')
        for item in samples
    ]
    widths = [
        max(len(headers[0]), *(len(row[0]) for row in rows)),
        max(len(headers[1]), *(len(row[1]) for row in rows)),
        max(len(headers[2]), *(len(row[2]) for row in rows)),
        max(len(headers[3]), *(len(row[3]) for row in rows)),
    ]

    def fmt_row(cols: tuple[str, str, str, str]) -> str:
        return (
            f'{cols[0]:<{widths[0]}}  '
            f'{cols[1]:>{widths[1]}}  '
            f'{cols[2]:>{widths[2]}}  '
            f'{cols[3]:>{widths[3]}}'
        )

    print(fmt_row(headers))
    print('-' * (sum(widths) + 6))
    for row in rows:
        print(fmt_row(row))

    total_memory_mb = round(sum(item.memory_mb for item in samples), 1)
    print('-' * (sum(widths) + 6))
    print(fmt_row((t('resource_usage_total', count=len(samples)), '', f'{total_memory_mb:.1f}', '')))


def render_once(root: Path, *, as_json: bool) -> int:
    samples = collect_samples(root)
    if as_json:
        print(json.dumps(build_payload(samples), ensure_ascii=False, indent=2))
    else:
        print_table(samples)
    return 0


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description=t('resource_usage_description'))
    parser.add_argument('--json', action='store_true', help=t('help_json_output'))
    parser.add_argument('-w', '--watch', action='store_true', help=t('help_watch_report'))
    parser.add_argument('--interval', type=float, default=3.0, help=t('help_watch_interval'))
    parser.add_argument('--root', default=None, help=t('help_project_root'))
    args = parser.parse_args()

    root = resolve_root(args.root)

    if args.watch:
        if args.interval <= 0:
            raise SystemExit(format_console('error', t('interval_must_be_positive')))
        try:
            while True:
                if not args.json and sys.stdout.isatty():
                    print('\033[H\033[J', end='')
                render_once(root, as_json=args.json)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print()
            return 0

    return render_once(root, as_json=args.json)


if __name__ == '__main__':
    raise SystemExit(main())
