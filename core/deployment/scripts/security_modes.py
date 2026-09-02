"""
Справка по режимам безопасности (этап 0, без изменения рантайма).

  ergoms security-modes
  ergoms security-modes --controls
  ergoms security-modes --profile hardened --json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t
from console_tags import configure_stdio_utf8, format_console
from security.cli_modes import run_security_modes

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description=t('security_modes_description'))
    parser.add_argument('--root', type=Path, default=None, help=t('security_help_root'))
    parser.add_argument(
        '--profile',
        default=None,
        help=t('security_help_profile'),
    )
    parser.add_argument(
        '--controls',
        action='store_true',
        help=t('security_modes_help_controls'),
    )
    parser.add_argument('--json', action='store_true', help=t('security_help_json'))
    args = parser.parse_args(argv)

    root = args.root or _PROJECT_ROOT
    return run_security_modes(
        root,
        profile=args.profile,
        show_controls=args.controls,
        as_json=args.json,
        format_console=format_console,
        t=t,
    )


if __name__ == '__main__':
    raise SystemExit(main())
