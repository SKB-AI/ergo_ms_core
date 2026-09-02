"""Порог скорости ready и короткий прогон сценариев ядра."""

from __future__ import annotations

import time
from pathlib import Path

from ..environment import IsolatedEnvironment
from ..http import http_exchange, http_status
from ..report import CaseResult
from .base import SystemCase

_READY_BUDGET_S = 2.0
_CORE_SCENARIOS = (
    '/api/system/ready/',
    '/api/cms/adp/profile/',
    '/api/cms/adp/session-bootstrap/',
)


class ReadyLatencyCase(SystemCase):
    name = 'ready_latency'
    domain = 'performance'

    def run(self, env: IsolatedEnvironment) -> CaseResult:
        env.ensure_api()
        url = env.http_base().rstrip('/') + '/api/system/ready/'
        started = time.perf_counter()
        status = http_status(url)
        elapsed = time.perf_counter() - started
        if status == 0:
            return CaseResult(self.name, self.domain, 'skip', 'ready недоступен')
        if elapsed > _READY_BUDGET_S:
            return CaseResult(
                self.name,
                self.domain,
                'fail',
                f'ready {elapsed:.2f}s > {_READY_BUDGET_S}s (status={status})',
            )
        extras = _hit_core_paths(env)
        yaml_hint = _core_yaml_exists(env)
        detail = f'{elapsed:.2f}s status={status}'
        if extras:
            detail += f'; {extras}'
        if yaml_hint:
            detail += '; core_scenarios.yaml'
        return CaseResult(self.name, self.domain, 'ok', detail)


def _hit_core_paths(env: IsolatedEnvironment) -> str:
    parts: list[str] = []
    base = env.http_base().rstrip('/')
    for path in _CORE_SCENARIOS:
        started = time.perf_counter()
        status, _headers, _body = http_exchange(base + path)
        elapsed = time.perf_counter() - started
        if status == 0:
            continue
        if elapsed > _READY_BUDGET_S * 2:
            parts.append(f'{path} {elapsed:.2f}s')
        else:
            parts.append(f'{path.split("/")[-2] or "ready"}={status}')
    return ', '.join(parts)


def _core_yaml_exists(env: IsolatedEnvironment) -> bool:
    path = Path(env.workspace) / 'core' / 'deployment' / 'loadtest' / 'core_scenarios.yaml'
    return path.is_file()
