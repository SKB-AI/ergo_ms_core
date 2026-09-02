"""
Поиск предела нагрузки: рост с 1 пользователя до поломки порогов.

Опциональный max_users — предохранитель (capped), не обязателен.
Пороги: p95/fail (без bootstrap), host CPU/RAM % и опционально ERGO RAM.
growth=exp: зонд ×2 (стоп после 2 подряд fail), затем binary refine до R−L≤1;
growth=linear: +step_users (стоп после 2 подряд fail).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from loadtest.find_limit_step import (  # noqa: E402
    ParsedStats,
    evaluate_step,
    parse_locust_stats_csv,
    run_locust_step,
)
from loadtest.resources import (  # noqa: E402
    DEFAULT_MAX_CPU_PERCENT,
    DEFAULT_MAX_ERGO_RAM_MB,
    DEFAULT_MAX_RAM_PERCENT,
    ResourceSample,
    sample_resources,
)

FIND_LIMIT_CONSECUTIVE_FAILS = 2
DEFAULT_MAX_P95_MS = 5000.0
# Точность binary refine для exp: midpoints считает алгоритм, шаг не задаётся.
EXP_REFINE_PRECISION = 1

GROWTH_EXP = 'exp'
GROWTH_LINEAR = 'linear'
PHASE_PROBE = 'probe'
PHASE_REFINE = 'refine'

# Re-export для внешних импортов
__all__ = [
    'DEFAULT_MAX_P95_MS',
    'EXP_REFINE_PRECISION',
    'FIND_LIMIT_CONSECUTIVE_FAILS',
    'GROWTH_EXP',
    'GROWTH_LINEAR',
    'PHASE_PROBE',
    'PHASE_REFINE',
    'FindLimitResult',
    'ParsedStats',
    'ResumeState',
    'StepResult',
    'clear_find_limit_dir',
    'evaluate_step',
    'find_limit',
    'load_resume_state',
    'parse_locust_stats_csv',
    'run_locust_step',
]


@dataclass
class StepResult:
    users: int
    ok: bool
    request_count: int
    failure_count: int
    fail_ratio: float
    p95_ms: float
    reason: str = ''
    csv_prefix: str = ''
    request_count_all: int = 0
    p95_ms_all: float = 0.0
    host_cpu_percent: float = 0.0
    host_ram_percent: float = 0.0
    ergo_memory_mb: float = 0.0
    ergo_cpu_percent: float = 0.0
    api_memory_mb: float = 0.0
    api_cpu_percent: float = 0.0
    phase: str = ''


@dataclass
class FindLimitResult:
    capacity: int | None
    broke_at: int | None
    status: str  # broke | capped | empty
    steps: list[StepResult] = field(default_factory=list)
    run_id: str = ''
    summary_path: str = ''


def _apply_resources_to_step(step: StepResult, resources: ResourceSample | None) -> None:
    if resources is None:
        return
    step.host_cpu_percent = resources.host_cpu_percent
    step.host_ram_percent = resources.host_ram_percent
    step.ergo_memory_mb = resources.ergo_memory_mb
    step.ergo_cpu_percent = resources.ergo_cpu_percent
    step.api_memory_mb = resources.api_memory_mb
    step.api_cpu_percent = resources.api_cpu_percent


def _next_user_count(
    n: int,
    *,
    step_users: int,
    max_users: int | None,
    growth: str = GROWTH_LINEAR,
) -> tuple[int | None, bool]:
    """
    Вернуть (next_n, capped).
    next_n=None и capped=True — достигли потолка без дальнейшего роста.
    exp: удвоение; linear: +step_users.
    """
    if growth == GROWTH_EXP:
        next_n = min(2 * n, max_users) if max_users is not None else 2 * n
        if max_users is not None and n >= max_users:
            return None, True
        if next_n <= n:
            return None, True
        return next_n, False

    next_n = n + step_users
    if max_users is not None and next_n > max_users:
        if n < max_users:
            return max_users, False
        return None, True
    return next_n, False


def _refine_midpoint(low: int, high: int) -> int | None:
    """Середина (L, R) для binary refine; None если дальше сужать нельзя."""
    if high - low <= 0:
        return None
    mid = low + (high - low) // 2
    if mid <= low or mid >= high:
        return None
    return mid


@dataclass(frozen=True)
class ResumeState:
    steps: tuple[StepResult, ...]
    start_n: int
    last_ok: int | None
    consecutive_fails: int
    broke_at: int | None = None
    growth: str = GROWTH_EXP
    phase: str = PHASE_PROBE


def clear_find_limit_dir(out_dir: Path) -> int:
    """Удалить файлы в каталоге find_limit (step_*, summary.json, …)."""
    if not out_dir.is_dir():
        return 0
    removed = 0
    for path in out_dir.iterdir():
        if path.is_file():
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
    return removed


def _step_from_dict(raw: dict[str, Any]) -> StepResult | None:
    try:
        users = int(raw.get('users') or 0)
    except (TypeError, ValueError):
        return None
    if users < 1:
        return None
    try:
        request_count = int(raw.get('request_count') or 0)
        failure_count = int(raw.get('failure_count') or 0)
        fail_ratio = float(raw.get('fail_ratio') or 0.0)
        p95_ms = float(raw.get('p95_ms') or 0.0)
        request_count_all = int(raw.get('request_count_all') or 0)
        p95_ms_all = float(raw.get('p95_ms_all') or 0.0)
        host_cpu_percent = float(raw.get('host_cpu_percent') or 0.0)
        host_ram_percent = float(raw.get('host_ram_percent') or 0.0)
        ergo_memory_mb = float(raw.get('ergo_memory_mb') or 0.0)
        ergo_cpu_percent = float(raw.get('ergo_cpu_percent') or 0.0)
        api_memory_mb = float(raw.get('api_memory_mb') or 0.0)
        api_cpu_percent = float(raw.get('api_cpu_percent') or 0.0)
    except (TypeError, ValueError):
        return None
    return StepResult(
        users=users,
        ok=bool(raw.get('ok')),
        request_count=request_count,
        failure_count=failure_count,
        fail_ratio=fail_ratio,
        p95_ms=p95_ms,
        reason=str(raw.get('reason') or ''),
        csv_prefix=str(raw.get('csv_prefix') or ''),
        request_count_all=request_count_all,
        p95_ms_all=p95_ms_all,
        host_cpu_percent=host_cpu_percent,
        host_ram_percent=host_ram_percent,
        ergo_memory_mb=ergo_memory_mb,
        ergo_cpu_percent=ergo_cpu_percent,
        api_memory_mb=api_memory_mb,
        api_cpu_percent=api_cpu_percent,
        phase=str(raw.get('phase') or ''),
    )


def load_resume_state(
    out_dir: Path,
    *,
    step_users: int,
    growth: str = GROWTH_EXP,
) -> ResumeState:
    """
    Прочитать summary.json для --resume.

    linear / probe: start_n = broke_at, иначе last_ok + next step.
    exp + last_ok + broke_at и R−L > EXP_REFINE_PRECISION → сразу refine.
    """
    summary_path = out_dir / 'summary.json'
    if not summary_path.is_file():
        raise FileNotFoundError(str(summary_path))
    try:
        data = json.loads(summary_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'invalid summary.json: {exc}') from exc
    if not isinstance(data, dict):
        raise ValueError('summary.json root must be object')

    steps_raw = data.get('steps')
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError('summary.json has no steps')

    steps: list[StepResult] = []
    for item in steps_raw:
        if not isinstance(item, dict):
            continue
        parsed = _step_from_dict(item)
        if parsed is not None:
            steps.append(parsed)
    if not steps:
        raise ValueError('summary.json has no valid steps')

    thresholds = data.get('thresholds') if isinstance(data.get('thresholds'), dict) else {}
    saved_growth = str(thresholds.get('growth') or growth).strip().lower()
    if saved_growth not in (GROWTH_EXP, GROWTH_LINEAR):
        saved_growth = growth

    last_ok: int | None = None
    for step in steps:
        if step.ok:
            last_ok = step.users
    capacity_raw = data.get('capacity')
    if capacity_raw is not None:
        try:
            capacity = int(capacity_raw)
        except (TypeError, ValueError):
            capacity = None
        else:
            if capacity >= 1:
                last_ok = capacity if last_ok is None else max(last_ok, capacity)

    broke_at: int | None = None
    broke_raw = data.get('broke_at')
    if broke_raw is not None:
        try:
            broke_at = int(broke_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'invalid broke_at: {broke_raw}') from exc

    phase = PHASE_PROBE
    consecutive_fails = 1 if not steps[-1].ok else 0
    if (
        saved_growth == GROWTH_EXP
        and last_ok is not None
        and broke_at is not None
        and broke_at - last_ok > EXP_REFINE_PRECISION
    ):
        phase = PHASE_REFINE
        mid = _refine_midpoint(last_ok, broke_at)
        start_n = mid if mid is not None else last_ok + 1
        consecutive_fails = 0
    elif broke_at is not None:
        start_n = broke_at
    elif last_ok is not None:
        next_n, _ = _next_user_count(
            last_ok,
            step_users=step_users,
            max_users=None,
            growth=saved_growth,
        )
        start_n = next_n if next_n is not None else last_ok + max(1, step_users)
        consecutive_fails = 0
    else:
        raise ValueError('cannot determine resume start_n')

    if start_n < 1:
        raise ValueError(f'invalid resume start_n={start_n}')

    return ResumeState(
        steps=tuple(steps),
        start_n=start_n,
        last_ok=last_ok,
        consecutive_fails=consecutive_fails,
        broke_at=broke_at,
        growth=saved_growth,
        phase=phase,
    )


def find_limit(
    *,
    root: Path,
    host: str,
    scenario_dicts: list[dict[str, Any]],
    target_names: list[str],
    ensure_users_fn: Callable[..., tuple[str, list[str]]],
    page_dicts: list[dict[str, Any]] | None = None,
    step_users: int = 1,
    step_time: str = '30s',
    max_fail_ratio: float = 0.01,
    max_p95_ms: float = DEFAULT_MAX_P95_MS,
    max_cpu_percent: float = DEFAULT_MAX_CPU_PERCENT,
    max_ram_percent: float = DEFAULT_MAX_RAM_PERCENT,
    max_ergo_ram_mb: float = DEFAULT_MAX_ERGO_RAM_MB,
    max_users: int | None = None,
    spawn_rate: int | None = None,
    out_dir: Path | None = None,
    resume: bool = False,
    growth: str = GROWTH_EXP,
    log: Callable[[str, str], None] | None = None,
) -> FindLimitResult:
    """
    ensure_users_fn(total, out_path, run_id, access_tokens) -> (run_id, tokens)
    log(level, message) — опционально (info/ok/warning/error).
    growth=exp: зонд ×2, затем binary refine до R−L≤1 (step_users не влияет).
    growth=linear: N += step_users.
    resume=False — очистить out_dir и начать с 1; True — продолжить по summary.json.
    """
    def _log(level: str, message: str) -> None:
        if log is not None:
            log(level, message)

    growth_mode = (growth or GROWTH_EXP).strip().lower()
    if growth_mode not in (GROWTH_EXP, GROWTH_LINEAR):
        raise ValueError(f'growth must be {GROWTH_EXP!r} or {GROWTH_LINEAR!r}')
    if step_users < 1:
        raise ValueError('step_users must be >= 1')
    if max_users is not None and max_users < 1:
        raise ValueError('max_users must be >= 1')

    out = out_dir or (root / 'logs' / 'loadtest' / 'find_limit')
    out.mkdir(parents=True, exist_ok=True)

    run_id: str | None = None
    access_tokens: list[str] = []
    steps: list[StepResult] = []
    last_ok: int | None = None
    n = 1
    status = 'empty'
    broke_at: int | None = None
    consecutive_fails = 0
    resumed = False
    start_users = 1
    phase = PHASE_PROBE
    rate = spawn_rate if spawn_rate is not None else min(step_users, 10)
    if rate < 1:
        rate = 1

    if resume:
        state = load_resume_state(out, step_users=step_users, growth=growth_mode)
        steps = list(state.steps)
        last_ok = state.last_ok
        n = state.start_n
        consecutive_fails = state.consecutive_fails
        broke_at = state.broke_at
        growth_mode = state.growth or growth_mode
        phase = state.phase
        resumed = True
        start_users = n
        if max_users is not None and n > max_users:
            raise ValueError(
                f'resume start_n={n} exceeds max_users={max_users}'
            )
        _log(
            'info',
            f'resume from users={n}, phase={phase}, growth={growth_mode}, '
            f'prior_steps={len(steps)}, consecutive_fails={consecutive_fails}',
        )
    else:
        removed = clear_find_limit_dir(out)
        out.mkdir(parents=True, exist_ok=True)
        _log('info', f'cleared find_limit dir ({removed} files)')
        if growth_mode == GROWTH_EXP:
            _log(
                'info',
                f'find-limit growth={growth_mode}, '
                f'refine_precision={EXP_REFINE_PRECISION}',
            )
        else:
            _log(
                'info',
                f'find-limit growth={growth_mode}, step_users={step_users}',
            )

    fd, provision_name = tempfile.mkstemp(
        prefix='ergo_loadtest_find_',
        suffix='.json',
    )
    os.close(fd)
    provision_path = Path(provision_name)

    def _execute_step(users: int, step_phase: str) -> StepResult:
        nonlocal run_id, access_tokens
        seq = len(steps) + 1
        _log('info', f'find-limit {step_phase} users={users}')
        run_id, access_tokens = ensure_users_fn(
            total=users,
            out_path=provision_path,
            run_id=run_id,
            access_tokens=access_tokens,
        )
        if len(access_tokens) < users:
            raise RuntimeError(
                f'ensure_users returned {len(access_tokens)} tokens, need {users}'
            )

        csv_prefix = out / f'step_{seq:03d}_u{users}'
        html_path = out / f'step_{seq:03d}_u{users}.html'
        payload = {
            'scenarios': scenario_dicts,
            'pages': page_dicts or [],
            'targets': target_names,
            'access_tokens': access_tokens[:users],
            'run_id': run_id,
        }
        run_locust_step(
            root=root,
            host=host,
            users=users,
            spawn_rate=rate,
            run_time=step_time,
            scenarios_payload=payload,
            csv_prefix=csv_prefix,
            html_path=html_path,
        )

        stats_path = Path(f'{csv_prefix}_stats.csv')
        resources = sample_resources(root)
        try:
            parsed = parse_locust_stats_csv(stats_path)
        except (OSError, ValueError) as exc:
            step = StepResult(
                users=users,
                ok=False,
                request_count=0,
                failure_count=0,
                fail_ratio=1.0,
                p95_ms=0.0,
                reason=f'stats_error:{exc}',
                csv_prefix=str(csv_prefix),
                phase=step_phase,
            )
            _apply_resources_to_step(step, resources)
            return step

        ok, fail_ratio, reason = evaluate_step(
            request_count=parsed.request_count,
            failure_count=parsed.failure_count,
            p95_ms=parsed.p95_ms,
            max_fail_ratio=max_fail_ratio,
            max_p95_ms=max_p95_ms,
            resources=resources,
            max_cpu_percent=max_cpu_percent,
            max_ram_percent=max_ram_percent,
            max_ergo_ram_mb=max_ergo_ram_mb,
        )
        step = StepResult(
            users=users,
            ok=ok,
            request_count=parsed.request_count,
            failure_count=parsed.failure_count,
            fail_ratio=fail_ratio,
            p95_ms=parsed.p95_ms,
            reason=reason,
            csv_prefix=str(csv_prefix),
            request_count_all=parsed.request_count_all,
            p95_ms_all=parsed.p95_ms_all,
            phase=step_phase,
        )
        _apply_resources_to_step(step, resources)
        if ok:
            _log(
                'ok',
                f'step {users} ok ({step_phase}): requests={parsed.request_count}, '
                f'fail_ratio={fail_ratio:.4f}, p95={parsed.p95_ms:.0f}ms, '
                f'cpu={resources.host_cpu_percent:.0f}%, '
                f'ram={resources.host_ram_percent:.0f}%, '
                f'ergo_ram={resources.ergo_memory_mb:.0f}MB',
            )
        else:
            _log(
                'warning',
                f'step {users} fail ({step_phase}): {reason}; '
                f'all p95={parsed.p95_ms_all:.0f}ms, '
                f'cpu={resources.host_cpu_percent:.0f}%, '
                f'ram={resources.host_ram_percent:.0f}%',
            )
        return step

    def _enter_refine() -> bool:
        """Перейти в binary refine. True — есть точка для прогона."""
        nonlocal phase, n, consecutive_fails
        if growth_mode != GROWTH_EXP:
            return False
        if last_ok is None or broke_at is None:
            return False
        if broke_at - last_ok <= EXP_REFINE_PRECISION:
            return False
        mid = _refine_midpoint(last_ok, broke_at)
        if mid is None:
            return False
        phase = PHASE_REFINE
        consecutive_fails = 0
        n = mid
        _log(
            'info',
            f'find-limit refine [{last_ok}, {broke_at}], mid={mid}, '
            f'precision={EXP_REFINE_PRECISION}',
        )
        return True

    try:
        while True:
            if phase == PHASE_REFINE:
                if last_ok is None or broke_at is None:
                    status = 'broke' if broke_at is not None else 'empty'
                    break
                if broke_at - last_ok <= EXP_REFINE_PRECISION:
                    status = 'broke'
                    _log(
                        'ok',
                        f'find-limit refined: capacity={last_ok}, '
                        f'broke_at={broke_at}',
                    )
                    break
                mid = _refine_midpoint(last_ok, broke_at)
                if mid is None:
                    status = 'broke'
                    break
                n = mid

            if max_users is not None and n > max_users:
                status = 'capped'
                _log('ok', f'find-limit capped at max-users={max_users}')
                break

            step = _execute_step(n, phase)
            steps.append(step)

            if phase == PHASE_REFINE:
                # В refine один fail достаточен.
                if step.ok:
                    last_ok = n
                    consecutive_fails = 0
                else:
                    broke_at = n
                    consecutive_fails = 0
                continue

            # --- probe / linear ---
            if step.ok:
                consecutive_fails = 0
                last_ok = n
                next_n, capped = _next_user_count(
                    n,
                    step_users=step_users,
                    max_users=max_users,
                    growth=growth_mode,
                )
                if capped or next_n is None:
                    status = 'capped'
                    _log(
                        'ok',
                        f'find-limit reached max-users={max_users} without break',
                    )
                    break
                n = next_n
                continue

            consecutive_fails += 1
            # probe/linear: стоп после 2 подряд; soft-fail продолжает рост.
            _log(
                'warning',
                f'step {n} broke ({consecutive_fails}/{FIND_LIMIT_CONSECUTIVE_FAILS}) '
                f'[{phase}]',
            )
            if consecutive_fails >= FIND_LIMIT_CONSECUTIVE_FAILS:
                broke_at = n
                if growth_mode == GROWTH_EXP and last_ok is not None:
                    if _enter_refine():
                        continue
                    status = 'broke'
                    break
                status = 'broke'
                break

            next_n, capped = _next_user_count(
                n,
                step_users=step_users,
                max_users=max_users,
                growth=growth_mode,
            )
            if capped or next_n is None:
                status = 'capped'
                _log(
                    'ok',
                    f'find-limit reached max-users={max_users} after soft fail',
                )
                break
            n = next_n
    finally:
        try:
            provision_path.unlink(missing_ok=True)
        except OSError:
            pass

    capacity = last_ok
    if status == 'empty' and last_ok is None and not steps:
        status = 'empty'
    elif status == 'empty' and broke_at is not None:
        status = 'broke'
    elif status == 'empty' and last_ok is not None:
        status = 'capped' if max_users is not None else 'broke'

    summary = FindLimitResult(
        capacity=capacity,
        broke_at=broke_at,
        status=status,
        steps=steps,
        run_id=run_id or '',
    )
    summary_path = out / 'summary.json'
    summary_path.write_text(
        json.dumps(
            {
                'capacity': summary.capacity,
                'broke_at': summary.broke_at,
                'status': summary.status,
                'run_id': summary.run_id,
                'resumed': resumed,
                'start_users': start_users,
                'thresholds': {
                    'max_fail_ratio': max_fail_ratio,
                    'max_p95_ms': max_p95_ms,
                    'max_cpu_percent': max_cpu_percent,
                    'max_ram_percent': max_ram_percent,
                    'max_ergo_ram_mb': max_ergo_ram_mb,
                    'step_users': step_users if growth_mode == GROWTH_LINEAR else None,
                    'step_time': step_time,
                    'max_users': max_users,
                    'growth': growth_mode,
                    'refine_precision': (
                        EXP_REFINE_PRECISION if growth_mode == GROWTH_EXP else None
                    ),
                    'consecutive_fails': FIND_LIMIT_CONSECUTIVE_FAILS,
                    'p95_excludes_bootstrap': True,
                },
                'steps': [asdict(s) for s in steps],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    summary.summary_path = str(summary_path)
    return summary
