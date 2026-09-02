"""Непрерывная подстройка без restart-loop."""

from __future__ import annotations

import time
from pathlib import Path

from cli_locale import t
from console_tags import format_console

from celery_balance.apply import apply_plans, should_skip_change
from celery_balance.constants import MODE_AUTO, MODE_RECOMMEND
from celery_balance.report import print_report
from celery_balance.service import collect_snapshot, persist_snapshot, previous_concurrency


def run_watch(
    project_root: Path,
    *,
    dry_run: bool,
    as_json: bool,
) -> int:
    while True:
        snapshot = collect_snapshot(project_root)
        prev = previous_concurrency(project_root)
        skip_all = True
        for plan in snapshot.plans:
            if not should_skip_change(
                prev.get(plan.name),
                plan.concurrency,
                snapshot.settings.hysteresis_ratio,
            ):
                skip_all = False
                break

        apply_overlay = (
            not dry_run
            and snapshot.settings.mode in {MODE_AUTO, MODE_RECOMMEND}
        )
        if apply_overlay and not skip_all:
            persist_snapshot(project_root, snapshot)
        elif apply_overlay and snapshot.settings.mode == MODE_RECOMMEND:
            persist_snapshot(project_root, snapshot)

        result = apply_plans(
            project_root,
            snapshot.plans,
            settings=snapshot.settings,
            previous=prev,
            dry_run=dry_run or skip_all,
            write_overlay=bool(snapshot.overlay_path),
        )
        if as_json:
            import json

            payload = snapshot.to_dict()
            payload['watch'] = {
                'skipped': skip_all,
                'live_applied': result.live_applied,
                'needs_restart': result.needs_restart,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if skip_all:
                print(format_console('info', t('celery_balance_watch_skip')))
            print_report(snapshot, dry_run=dry_run)
            if result.needs_restart and snapshot.settings.mode == MODE_AUTO:
                print(format_console('info', t('celery_balance_restart_hint')))
        time.sleep(snapshot.settings.watch_interval_sec)
