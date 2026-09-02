"""CLI: ergoms celery-balance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _DEPLOYMENT_DIR / 'scripts'
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from ergo_process_classifier import PROJECT_ROOT  # noqa: E402

from celery_balance.apply import apply_plans  # noqa: E402
from celery_balance.constants import MODE_AUTO, MODE_RECOMMEND  # noqa: E402
from celery_balance.report import print_report  # noqa: E402
from celery_balance.service import (  # noqa: E402
    collect_snapshot,
    persist_snapshot,
    previous_concurrency,
)
from celery_balance.watch import run_watch  # noqa: E402


def resolve_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise SystemExit(format_console('error', t('project_dir_not_found', root=root)))
        return root
    if (PROJECT_ROOT / 'pyproject.toml').is_file():
        return PROJECT_ROOT
    raise SystemExit(format_console('error', t('project_root_resolve_failed')))


def _run_once(root: Path, *, dry_run: bool, as_json: bool) -> int:
    snapshot = collect_snapshot(root)
    write_overlay = (not dry_run) and snapshot.settings.mode in {MODE_RECOMMEND, MODE_AUTO}
    if write_overlay:
        persist_snapshot(root, snapshot)

    prev = previous_concurrency(root)
    result = apply_plans(
        root,
        snapshot.plans,
        settings=snapshot.settings,
        previous=prev,
        dry_run=dry_run,
        write_overlay=write_overlay,
    )
    if as_json:
        payload = snapshot.to_dict()
        payload['apply'] = {
            'live_applied': result.live_applied,
            'needs_restart': result.needs_restart,
            'detail': result.detail,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_report(snapshot, dry_run=dry_run)
        if result.needs_restart and snapshot.settings.mode == MODE_AUTO and not dry_run:
            print(format_console('info', t('celery_balance_restart_hint')))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description=t('celery_balance_description'))
    parser.add_argument('--dry-run', action='store_true', help=t('celery_balance_help_dry_run'))
    parser.add_argument('--watch', action='store_true', help=t('help_watch_report'))
    parser.add_argument('--json', action='store_true', help=t('help_json_output'))
    parser.add_argument('--interval', type=float, default=None, help=t('help_watch_interval'))
    parser.add_argument('--root', default=None, help=t('help_project_root'))
    args = parser.parse_args(argv)

    root = resolve_root(args.root)
    if args.watch:
        if args.interval is not None:
            if args.interval <= 0:
                raise SystemExit(format_console('error', t('interval_must_be_positive')))
            import os

            os.environ['CELERY_BALANCE_WATCH_INTERVAL'] = str(args.interval)
        try:
            return run_watch(root, dry_run=args.dry_run, as_json=args.json)
        except KeyboardInterrupt:
            print()
            return 0
    return _run_once(root, dry_run=args.dry_run, as_json=args.json)


if __name__ == '__main__':
    raise SystemExit(main())
