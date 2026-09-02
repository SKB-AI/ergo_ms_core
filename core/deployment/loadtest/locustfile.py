"""
Locust HttpUser для ERGO MS.

Сценарии/страницы и токены передаются через ERGO_LOADTEST_SCENARIOS_FILE (JSON):
- scenarios — atomic HTTP-сценарии
- pages — бандлы запросов (page fan-out)
- access_tokens — по одному JWT на виртуального пользователя
"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from loadtest.auth import apply_bearer  # noqa: E402

try:
    from locust import HttpUser, between, task
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        'locust is not installed; run: ergoms poetry install --with loadtest'
    ) from exc

try:
    from gevent import joinall, spawn
except ImportError:  # pragma: no cover
    joinall = None  # type: ignore[assignment]
    spawn = None  # type: ignore[assignment]


@dataclass(frozen=True)
class RuntimeScenario:
    id: str
    method: str
    path: str
    weight: int
    auth: str
    expect_status: tuple[int, ...]
    query: dict[str, str]
    json: dict[str, Any] | None = None


@dataclass(frozen=True)
class RuntimePageRequest:
    id: str
    method: str
    path: str
    auth: str
    expect_status: tuple[int, ...]
    query: dict[str, str]
    json: dict[str, Any] | None = None


@dataclass(frozen=True)
class RuntimePage:
    id: str
    weight: int
    mode: str
    parallel: bool
    requests: tuple[RuntimePageRequest, ...]


def _load_payload() -> dict[str, Any]:
    path_raw = (os.environ.get('ERGO_LOADTEST_SCENARIOS_FILE') or '').strip()
    if not path_raw:
        return {}
    path = Path(path_raw)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_query(raw: Any) -> dict[str, str]:
    query: dict[str, str] = {}
    if not isinstance(raw, dict):
        return query
    for key, value in raw.items():
        if key is None or value is None:
            continue
        query[str(key)] = str(value)
    return query


def _parse_expect(raw: Any) -> tuple[int, ...]:
    if not isinstance(raw, list):
        return (200,)
    expect = tuple(int(x) for x in raw if isinstance(x, (int, float, str)))
    return expect or (200,)


def _parse_json_body(raw: dict[str, Any]) -> dict[str, Any] | None:
    if 'json' not in raw:
        return None
    value = raw.get('json')
    return value if isinstance(value, dict) else None


def _parse_scenarios(raw_list: Any) -> list[RuntimeScenario]:
    if not isinstance(raw_list, list):
        return []
    result: list[RuntimeScenario] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        sid = str(item.get('id') or '').strip()
        method = str(item.get('method') or 'GET').strip().upper()
        path_val = str(item.get('path') or '').strip()
        if not sid or not path_val:
            continue
        if 'json' in item and not isinstance(item.get('json'), dict):
            continue
        weight = int(item.get('weight') or 1)
        auth = str(item.get('auth') or 'bearer').strip().lower()
        result.append(
            RuntimeScenario(
                id=sid,
                method=method,
                path=path_val if path_val.startswith('/') else f'/{path_val}',
                weight=max(1, weight),
                auth=auth,
                expect_status=_parse_expect(item.get('expect_status')),
                query=_parse_query(item.get('query')),
                json=_parse_json_body(item),
            )
        )
    return result


def _parse_pages(raw_list: Any) -> list[RuntimePage]:
    if not isinstance(raw_list, list):
        return []
    result: list[RuntimePage] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        page_id = str(item.get('id') or '').strip()
        if not page_id:
            continue
        try:
            weight = max(1, int(item.get('weight') or 1))
        except (TypeError, ValueError):
            weight = 1
        mode = str(item.get('mode') or 'warm').strip().lower()
        if mode not in ('cold', 'warm'):
            mode = 'warm'
        parallel_raw = item.get('parallel')
        parallel = True if parallel_raw is None else bool(parallel_raw)
        reqs_raw = item.get('requests')
        if not isinstance(reqs_raw, list):
            continue
        requests: list[RuntimePageRequest] = []
        for req in reqs_raw:
            if not isinstance(req, dict):
                continue
            rid = str(req.get('id') or '').strip()
            method = str(req.get('method') or 'GET').strip().upper()
            path_val = str(req.get('path') or '').strip()
            if not rid or not path_val:
                continue
            if 'json' in req and not isinstance(req.get('json'), dict):
                continue
            auth = str(req.get('auth') or 'bearer').strip().lower()
            requests.append(
                RuntimePageRequest(
                    id=rid,
                    method=method,
                    path=path_val if path_val.startswith('/') else f'/{path_val}',
                    auth=auth,
                    expect_status=_parse_expect(req.get('expect_status')),
                    query=_parse_query(req.get('query')),
                    json=_parse_json_body(req),
                )
            )
        if not requests:
            continue
        result.append(
            RuntimePage(
                id=page_id,
                weight=weight,
                mode=mode,
                parallel=parallel,
                requests=tuple(requests),
            )
        )
    return result


def _api_url(path: str, query: dict[str, str]) -> str:
    normalized = path if path.startswith('/') else f'/{path}'
    if not normalized.startswith('/api/'):
        normalized = f'/api{normalized}'
    if query:
        return f'{normalized}?{urlencode(query)}'
    return normalized


_PAYLOAD = _load_payload()
_SCENARIOS = _parse_scenarios(_PAYLOAD.get('scenarios'))
_PAGES = _parse_pages(_PAYLOAD.get('pages'))
# Единый пул: иначе пустой @task atomic/page съедал бы половину итераций.
_POOL: list[tuple[str, Any]] = []
_POOL_WEIGHTS: list[int] = []
for _scenario in _SCENARIOS:
    _POOL.append(('scenario', _scenario))
    _POOL_WEIGHTS.append(_scenario.weight)
for _page in _PAGES:
    _POOL.append(('page', _page))
    _POOL_WEIGHTS.append(_page.weight)
_ACCESS_TOKENS = [
    str(tok).strip()
    for tok in (_PAYLOAD.get('access_tokens') or [])
    if str(tok).strip()
]
_TOKEN_LOCK = threading.Lock()
_TOKEN_INDEX = 0
_NEED_WARM_BOOTSTRAP = any(p.mode == 'warm' for p in _PAGES)


def _claim_access_token() -> str | None:
    """Выдать следующий JWT (round-robin, потокобезопасно)."""
    global _TOKEN_INDEX
    if not _ACCESS_TOKENS:
        return None
    with _TOKEN_LOCK:
        token = _ACCESS_TOKENS[_TOKEN_INDEX % len(_ACCESS_TOKENS)]
        _TOKEN_INDEX += 1
        return token


class ErgoLoadUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        needs_auth = (
            any(s.auth == 'bearer' for s in _SCENARIOS)
            or any(r.auth == 'bearer' for p in _PAGES for r in p.requests)
            or _NEED_WARM_BOOTSTRAP
        )
        if needs_auth:
            token = _claim_access_token()
            if not token:
                raise RuntimeError(
                    'access_tokens missing in ERGO_LOADTEST_SCENARIOS_FILE '
                    '(provision users before Locust)'
                )
            apply_bearer(self.client, token)

        if _NEED_WARM_BOOTSTRAP:
            self._execute_http(
                method='GET',
                path='/cms/adp/session-bootstrap/',
                query={},
                expect_status=(200,),
                name='vu_bootstrap GET /cms/adp/session-bootstrap/',
                json_body=None,
            )

    @task
    def run_weighted_item(self) -> None:
        if not _POOL:
            return
        kind, item = random.choices(_POOL, weights=_POOL_WEIGHTS, k=1)[0]
        if kind == 'scenario':
            self._execute_scenario(item)
        else:
            self._execute_page(item)

    def _execute_scenario(self, scenario: RuntimeScenario) -> None:
        self._execute_http(
            method=scenario.method,
            path=scenario.path,
            query=scenario.query,
            expect_status=scenario.expect_status,
            name=f'{scenario.id} {scenario.method} {scenario.path}',
            json_body=scenario.json,
        )

    def _execute_page(self, page: RuntimePage) -> None:
        if page.parallel and spawn is not None and joinall is not None and len(page.requests) > 1:
            greenlets = [
                spawn(self._execute_page_request, page, req) for req in page.requests
            ]
            joinall(greenlets)
            return
        for req in page.requests:
            self._execute_page_request(page, req)

    def _execute_page_request(self, page: RuntimePage, req: RuntimePageRequest) -> None:
        self._execute_http(
            method=req.method,
            path=req.path,
            query=req.query,
            expect_status=req.expect_status,
            name=f'{page.id}/{req.id} {req.method} {req.path}',
            json_body=req.json,
        )

    def _execute_http(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, str],
        expect_status: tuple[int, ...],
        name: str,
        json_body: dict[str, Any] | None = None,
    ) -> None:
        url = _api_url(path, query)
        request_fn = getattr(self.client, method.lower(), None)
        if request_fn is None:
            return
        method_u = method.upper()
        kwargs: dict[str, Any] = {'name': name, 'catch_response': True}
        if json_body is not None and method_u not in ('GET', 'HEAD'):
            kwargs['json'] = json_body
        with request_fn(url, **kwargs) as response:
            if response.status_code in expect_status:
                response.success()
            else:
                response.failure(
                    f'expected {expect_status}, got {response.status_code}'
                )
