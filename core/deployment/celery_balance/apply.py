"""Применение решения к уже запущенному пулу (soft, без kill-loop)."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from celery_balance.constants import MODE_OFF
from celery_balance.planner import WorkerPlan
from celery_balance.settings import BalanceSettings


@dataclass(frozen=True)
class ApplyResult:
    wrote_overlay: bool
    live_applied: bool
    needs_restart: bool
    detail: str


def _api_dir(project_root: Path) -> Path:
    return project_root / 'core' / 'api'


def try_pool_resize(
    project_root: Path,
    plan: WorkerPlan,
    previous: int | None,
) -> bool:
    """Celery control pool_grow/pool_shrink — только prefork; иначе False."""
    if plan.pool != 'prefork':
        return False
    if previous is None or previous == plan.concurrency:
        return False
    delta = plan.concurrency - previous
    action = 'pool_grow' if delta > 0 else 'pool_shrink'
    amount = abs(delta)
    python = sys.executable
    env = os.environ.copy()
    env.setdefault('PYTHONPATH', '')
    env['PYTHONPATH'] = str(project_root) + (
        os.pathsep + env['PYTHONPATH'] if env['PYTHONPATH'] else ''
    )
    try:
        result = subprocess.run(
            [
                python,
                '-m',
                'celery',
                '-A',
                'src',
                'control',
                '--timeout=3',
                action,
                str(amount),
            ],
            cwd=str(_api_dir(project_root)),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def apply_plans(
    project_root: Path,
    plans: list[WorkerPlan],
    *,
    settings: BalanceSettings,
    previous: dict[str, int],
    dry_run: bool,
    write_overlay: bool,
) -> ApplyResult:
    if dry_run or not settings.apply_overlay:
        return ApplyResult(
            wrote_overlay=write_overlay and not dry_run and settings.mode != MODE_OFF,
            live_applied=False,
            needs_restart=False,
            detail='dry-run' if dry_run else settings.mode,
        )

    live = False
    needs_restart = False
    for plan in plans:
        prev = previous.get(plan.name)
        if prev == plan.concurrency:
            continue
        if try_pool_resize(project_root, plan, prev):
            live = True
        else:
            needs_restart = True
    return ApplyResult(
        wrote_overlay=True,
        live_applied=live,
        needs_restart=needs_restart,
        detail='auto',
    )


def should_skip_change(
    previous: int | None,
    planned: int,
    hysteresis_ratio: float,
) -> bool:
    if previous is None:
        return False
    if previous == planned:
        return True
    delta = abs(planned - previous)
    if delta < 1:
        return True
    if previous > 0 and (delta / previous) <= hysteresis_ratio and delta < 2:
        return True
    return False
