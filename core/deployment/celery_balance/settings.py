"""Режим и knobs балансировщика из env (без записи в .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from env_file_loader import load_project_env

from celery_balance.constants import (
    DEFAULT_GPU_UTIL_CAP,
    DEFAULT_HYSTERESIS_RATIO,
    DEFAULT_MIN_CONCURRENCY,
    DEFAULT_OS_RESERVE_RAM_MB,
    DEFAULT_RESERVE_CPU,
    DEFAULT_MODE,
    DEFAULT_WATCH_INTERVAL_SEC,
    MODE_AUTO,
    MODE_OFF,
    MODE_RECOMMEND,
    MODES,
)


def _pick(environ: dict[str, str], key: str, default: str = '') -> str:
    raw = os.environ.get(key)
    if raw is not None and str(raw).strip() != '':
        return str(raw).strip()
    value = environ.get(key, default)
    return str(value).strip() if value is not None else default


def _as_int(raw: str, default: int | None) -> int | None:
    text = (raw or '').strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _as_float(raw: str, default: float) -> float:
    text = (raw or '').strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


@dataclass(frozen=True)
class BalanceSettings:
    mode: str
    os_reserve_ram_mb: int
    reserve_cpu: float
    min_concurrency: int
    max_concurrency: int | None
    gpu_enabled: bool
    gpu_util_cap: float
    watch_interval_sec: float
    hysteresis_ratio: float

    @property
    def apply_overlay(self) -> bool:
        return self.mode == MODE_AUTO

    @property
    def compute_plan(self) -> bool:
        return self.mode in {MODE_RECOMMEND, MODE_AUTO}


def load_settings(project_root: Path) -> BalanceSettings:
    environ = load_project_env(project_root)
    mode = _pick(environ, 'CELERY_BALANCE', DEFAULT_MODE).lower()
    if mode in {'advise', 'disabled', 'false', '0', 'no'}:
        mode = MODE_OFF
    if mode not in MODES:
        mode = DEFAULT_MODE

    gpu_raw = _pick(environ, 'CELERY_BALANCE_GPU', 'auto').lower()
    gpu_enabled = gpu_raw not in {'off', 'false', '0', 'no'}

    max_conc = _as_int(_pick(environ, 'CELERY_BALANCE_MAX_CONCURRENCY'), None)
    if max_conc is not None and max_conc < 1:
        max_conc = 1

    min_conc = _as_int(
        _pick(environ, 'CELERY_BALANCE_MIN_CONCURRENCY'),
        DEFAULT_MIN_CONCURRENCY,
    )
    if min_conc is None or min_conc < 1:
        min_conc = DEFAULT_MIN_CONCURRENCY

    os_reserve = _as_int(
        _pick(environ, 'CELERY_BALANCE_OS_RESERVE_RAM_MB'),
        DEFAULT_OS_RESERVE_RAM_MB,
    )
    if os_reserve is None or os_reserve < 0:
        os_reserve = DEFAULT_OS_RESERVE_RAM_MB

    return BalanceSettings(
        mode=mode,
        os_reserve_ram_mb=os_reserve,
        reserve_cpu=max(
            0.0,
            _as_float(_pick(environ, 'CELERY_BALANCE_RESERVE_CPU'), DEFAULT_RESERVE_CPU),
        ),
        min_concurrency=min_conc,
        max_concurrency=max_conc,
        gpu_enabled=gpu_enabled,
        gpu_util_cap=max(
            0.0,
            min(
                100.0,
                _as_float(
                    _pick(environ, 'CELERY_BALANCE_GPU_UTIL_CAP'),
                    DEFAULT_GPU_UTIL_CAP,
                ),
            ),
        ),
        watch_interval_sec=max(
            5.0,
            _as_float(
                _pick(environ, 'CELERY_BALANCE_WATCH_INTERVAL'),
                DEFAULT_WATCH_INTERVAL_SEC,
            ),
        ),
        hysteresis_ratio=max(
            0.0,
            _as_float(
                _pick(environ, 'CELERY_BALANCE_HYSTERESIS'),
                DEFAULT_HYSTERESIS_RATIO,
            ),
        ),
    )
