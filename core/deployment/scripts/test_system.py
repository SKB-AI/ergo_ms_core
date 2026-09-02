#!/usr/bin/env python3
"""Безопасный прогон проверок развёртывания на Windows и Linux.

Всегда запускает unit-тесты ``core/deployment/tests``.
Флаг ``--with-scenario`` добавляет изолированный сценарий
(по умолчанию ``host_sqlite_direct``) и не ставит службы ОС.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent
_TESTS_DIR = _DEPLOYMENT_DIR / 'tests'
_SCENARIO_SCRIPT = _SCRIPTS_DIR / 'deployment_scenario_test.py'

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402


def _run(argv: list[str]) -> int:
    return subprocess.call(argv, cwd=str(_PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Safe deployment checks (unit tests; optional isolated scenario)',
    )
    parser.add_argument(
        '--with-scenario',
        action='store_true',
        help='also run an isolated scenario (does not install OS services)',
    )
    parser.add_argument(
        '--spec',
        default='host_sqlite_direct',
        help='scenario id for --with-scenario (default: host_sqlite_direct)',
    )
    args = parser.parse_args(argv)
    configure_stdio_utf8()

    print(format_console('info', t('test_system_unit_start')))
    unit_code = _run([
        sys.executable,
        '-m',
        'unittest',
        'discover',
        '-s',
        str(_TESTS_DIR),
        '-p',
        'test_*.py',
        '-q',
    ])
    if unit_code != 0:
        print(format_console('error', t('test_system_unit_failed')), file=sys.stderr)
        return unit_code
    print(format_console('ok', t('test_system_unit_ok')))

    if not args.with_scenario:
        print(format_console('info', t('test_system_host_services_hint')))
        return 0

    print(format_console('info', t('test_system_scenario_start', spec=args.spec)))
    scenario_code = _run([
        sys.executable,
        str(_SCENARIO_SCRIPT),
        '--spec',
        args.spec,
    ])
    if scenario_code != 0:
        print(format_console('error', t('test_system_scenario_failed')), file=sys.stderr)
        return scenario_code
    print(format_console('ok', t('test_system_scenario_ok')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
