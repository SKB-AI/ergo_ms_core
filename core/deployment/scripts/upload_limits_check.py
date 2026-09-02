#!/usr/bin/env python3
"""
Отчёт по лимитам загрузки: MEDIA_UPLOAD_MAX_SIZE / HARD_MAX, direct-upload, nginx.

Использование: ergoms upload-limits-check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from env_file_loader import load_project_env  # noqa: E402
from upload_limits import build_upload_limits_report, format_mib  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='Upload limits report')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    env = load_project_env(args.root)
    report = build_upload_limits_report(env)

    print(format_console(
        'info',
        t(
            'upload_limits_media',
            size=format_mib(report['media_bytes']),
        ),
    ))
    print(format_console(
        'info',
        t(
            'upload_limits_hard',
            size=format_mib(report['hard_bytes']),
        ),
    ))
    for key, nbytes in report['direct_limits']:
        print(format_console(
            'info',
            t('upload_limits_direct', env_key=key, size=format_mib(nbytes)),
        ))
    print(format_console(
        'info',
        t(
            'upload_limits_nginx',
            size=report['nginx_size'],
            margin=report['margin_percent'],
        ),
    ))

    errors = 0
    hard_label = format_mib(report['hard_bytes'])
    default_label = format_mib(report['media_bytes'])
    for mod in report['modules']:
        size = format_mib(mod['bytes'])
        if mod['ok']:
            ceiling = hard_label if mod.get('above_default') else default_label
            print(format_console(
                'ok',
                t(
                    'upload_limits_module_ok',
                    module=mod['module'],
                    size=size,
                    platform=ceiling,
                ),
            ))
        else:
            errors += 1
            print(format_console(
                'error',
                t(
                    'upload_limits_module_over',
                    module=mod['module'],
                    env_key=mod['key'],
                    size=size,
                    platform=hard_label,
                ),
            ))

    for warning in report['warnings']:
        print(format_console('warning', warning))
    for err in report.get('errors') or []:
        errors += 1
        print(format_console('error', err))

    if errors:
        print(format_console('error', t('upload_limits_failed', count=errors)))
        return 1

    print(format_console('ok', t('upload_limits_ok')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
