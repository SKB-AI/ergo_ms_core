"""
Один шаг find-limit: прогон Locust, разбор stats CSV, оценка порогов.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from loadtest.resources import ResourceSample  # noqa: E402

_LOCUSTFILE = Path(__file__).resolve().parent / 'locustfile.py'


@dataclass(frozen=True)
class ParsedStats:
    request_count: int
    failure_count: int
    p95_ms: float
    request_count_all: int
    failure_count_all: int
    p95_ms_all: float


def _row_num(row: dict[str, str], key_candidates: tuple[str, ...], default: float = 0.0) -> float:
    for key in key_candidates:
        if key in row and row[key] not in (None, ''):
            try:
                return float(str(row[key]).replace(',', ''))
            except ValueError:
                continue
    lower_map = {k.lower(): v for k, v in row.items()}
    for key in key_candidates:
        raw = lower_map.get(key.lower())
        if raw not in (None, ''):
            try:
                return float(str(raw).replace(',', ''))
            except ValueError:
                continue
    return default


def is_bootstrap_stat_name(name: str) -> bool:
    """Исключить session-bootstrap / vu_bootstrap / page …/bootstrap из порогов."""
    lowered = name.strip().lower()
    if not lowered or lowered == 'aggregated':
        return False
    if lowered.startswith('vu_bootstrap'):
        return True
    if 'session-bootstrap' in lowered or 'session_bootstrap' in lowered:
        return True
    if '/bootstrap ' in lowered or lowered.endswith('/bootstrap'):
        return True
    return False


def parse_locust_stats_csv(stats_path: Path) -> ParsedStats:
    """
    Читает Locust *_stats.csv.

    Пороги: сумма counts и max(95%) по endpoint без bootstrap.
    Aggregated → request_count_all / p95_ms_all для прозрачности.
    """
    if not stats_path.is_file():
        raise FileNotFoundError(str(stats_path))

    with stats_path.open(encoding='utf-8', newline='') as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f'empty CSV headers: {stats_path}')
        rows = list(reader)

    count_keys = ('Request Count', 'RequestCount', '# requests')
    fail_keys = ('Failure Count', 'FailureCount', '# fails')
    p95_keys = ('95%', '95%ile', 'Ninety Fifth Percentile')

    aggregated: dict[str, str] | None = None
    request_count = 0
    failure_count = 0
    p95_values: list[float] = []

    for row in rows:
        name = (row.get('Name') or row.get('name') or '').strip()
        if name == 'Aggregated':
            aggregated = row
            continue
        if not name or is_bootstrap_stat_name(name):
            continue
        req = int(_row_num(row, count_keys, 0))
        fail = int(_row_num(row, fail_keys, 0))
        if req <= 0:
            continue
        request_count += req
        failure_count += fail
        p95_values.append(_row_num(row, p95_keys, 0.0))

    if aggregated is None:
        raise ValueError(f'no Aggregated row in {stats_path}')

    request_count_all = int(_row_num(aggregated, count_keys, 0))
    failure_count_all = int(_row_num(aggregated, fail_keys, 0))
    p95_ms_all = _row_num(aggregated, p95_keys, 0.0)
    p95_ms = max(p95_values) if p95_values else 0.0

    return ParsedStats(
        request_count=request_count,
        failure_count=failure_count,
        p95_ms=p95_ms,
        request_count_all=request_count_all,
        failure_count_all=failure_count_all,
        p95_ms_all=p95_ms_all,
    )


def evaluate_step(
    *,
    request_count: int,
    failure_count: int,
    p95_ms: float,
    max_fail_ratio: float,
    max_p95_ms: float,
    resources: ResourceSample | None = None,
    max_cpu_percent: float = 0.0,
    max_ram_percent: float = 0.0,
    max_ergo_ram_mb: float = 0.0,
) -> tuple[bool, float, str]:
    """Вернуть (ok, fail_ratio, reason). Порог 0 = проверка ресурсов отключена."""
    if request_count <= 0:
        return False, 1.0, 'no_requests'
    fail_ratio = failure_count / request_count
    if fail_ratio > max_fail_ratio:
        return False, fail_ratio, f'fail_ratio={fail_ratio:.4f}>{max_fail_ratio}'
    if p95_ms > max_p95_ms:
        return False, fail_ratio, f'p95_ms={p95_ms:.0f}>{max_p95_ms}'
    if resources is not None:
        if max_cpu_percent > 0 and resources.host_cpu_percent > max_cpu_percent:
            return (
                False,
                fail_ratio,
                f'host_cpu={resources.host_cpu_percent:.1f}>{max_cpu_percent}',
            )
        if max_ram_percent > 0 and resources.host_ram_percent > max_ram_percent:
            return (
                False,
                fail_ratio,
                f'host_ram={resources.host_ram_percent:.1f}%>{max_ram_percent}',
            )
        if max_ergo_ram_mb > 0 and resources.ergo_memory_mb > max_ergo_ram_mb:
            return (
                False,
                fail_ratio,
                f'ergo_ram_mb={resources.ergo_memory_mb:.0f}>{max_ergo_ram_mb}',
            )
    return True, fail_ratio, ''


def run_locust_step(
    *,
    root: Path,
    host: str,
    users: int,
    spawn_rate: int,
    run_time: str,
    scenarios_payload: dict[str, Any],
    csv_prefix: Path,
    html_path: Path,
) -> int:
    """Один headless-прогон Locust; returncode процесса."""
    csv_prefix.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        suffix='.json',
        prefix='ergo_loadtest_step_',
        delete=False,
    ) as tmp:
        json.dump(scenarios_payload, tmp, ensure_ascii=False)
        scenarios_file = tmp.name

    child_env = os.environ.copy()
    child_env['ERGO_LOADTEST_SCENARIOS_FILE'] = scenarios_file
    cmd = [
        sys.executable,
        '-m',
        'locust',
        '-f',
        str(_LOCUSTFILE),
        '--host',
        host,
        '-u',
        str(users),
        '-r',
        str(spawn_rate),
        '--headless',
        '-t',
        str(run_time),
        '--csv',
        str(csv_prefix),
        '--html',
        str(html_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            env=child_env,
            check=False,
        )
        return result.returncode
    finally:
        try:
            Path(scenarios_file).unlink(missing_ok=True)
        except OSError:
            pass
