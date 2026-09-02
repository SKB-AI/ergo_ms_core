#!/usr/bin/env python3
"""Единая точка ergoms system-test: unit + изолированные живые сьюты."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent
_TESTS_DIR = _DEPLOYMENT_DIR / 'tests'

for _path in (
    _DEPLOYMENT_DIR,
    _DEPLOYMENT_DIR / 'docker',
    _DEPLOYMENT_DIR / 'nginx',
    _DEPLOYMENT_DIR / 'lifecycle',
    _SCRIPTS_DIR,
):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from system_test.catalog import LIVE_SUITES, SUITE_ALL, SUITE_UNIT  # noqa: E402
from system_test.suite import SystemSuite, print_report  # noqa: E402


def _run_unit() -> int:
    print(format_console('info', t('test_system_unit_start')))
    code = subprocess.call(
        [
            sys.executable,
            '-m',
            'unittest',
            'discover',
            '-s',
            str(_TESTS_DIR),
            '-p',
            'test_*.py',
            '-q',
        ],
        cwd=str(_PROJECT_ROOT),
    )
    if code != 0:
        print(format_console('error', t('test_system_unit_failed')), file=sys.stderr)
        return code
    print(format_console('ok', t('test_system_unit_ok')))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Isolated ERGO MS system tests')
    parser.add_argument(
        '--suite',
        default=SUITE_UNIT,
        choices=(SUITE_UNIT, SUITE_ALL, *LIVE_SUITES),
    )
    parser.add_argument(
        '--launch',
        default='host',
        choices=('docker', 'host', 'os-services'),
    )
    parser.add_argument('--spec', action='append', default=[])
    args = parser.parse_args(argv)
    configure_stdio_utf8()

    if args.suite == SUITE_UNIT:
        return _run_unit()

    if args.suite == SUITE_ALL:
        unit_code = _run_unit()
        if unit_code != 0:
            return unit_code

    print(format_console('info', t('system_test_live_start', suite=args.suite, launch=args.launch)))
    report = SystemSuite(_PROJECT_ROOT).run(
        suite=args.suite,
        launch=args.launch,
        spec_ids=args.spec or None,
    )
    print_report(report)
    if report.exit_code() != 0:
        print(format_console('error', t('system_test_live_failed')), file=sys.stderr)
        return 1
    print(format_console('ok', t('system_test_live_ok')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
