"""HTTP-проверки изолированного стека."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

PROBE_SCRIPT = (
    'import os, sys, urllib.error, urllib.request\n'
    'for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):\n'
    '    os.environ.pop(key, None)\n'
    'os.environ["NO_PROXY"] = "*"\n'
    'os.environ["no_proxy"] = "*"\n'
    'url = sys.argv[1]\n'
    'method = sys.argv[2].upper() if len(sys.argv) > 2 else "GET"\n'
    'req = urllib.request.Request(url, method=method)\n'
    'auth = os.environ.get("PROBE_AUTH", "")\n'
    'if auth:\n'
    '    req.add_header("Authorization", auth)\n'
    'payload = os.environ.get("PROBE_JSON", "")\n'
    'data = payload.encode("utf-8") if payload else None\n'
    'if data:\n'
    '    req.add_header("Content-Type", "application/json")\n'
    '    req.data = data\n'
    'opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))\n'
    'try:\n'
    '    with opener.open(req, timeout=20) as resp:\n'
    '        status, body = resp.status, resp.read()\n'
    'except urllib.error.HTTPError as exc:\n'
    '    status = exc.code\n'
    '    body = exc.read() if exc.fp else b""\n'
    'print(status)\n'
    'sys.stdout.buffer.write(body[:65536])\n'
)


@dataclass(frozen=True)
class CoreHttpCase:
    case_id: str
    method: str
    path: str
    auth: str
    expect_status: tuple[int, ...]
    json_body: dict[str, Any] | None


def write_probe_script(path: Path) -> None:
    path.write_text(PROBE_SCRIPT, encoding='utf-8')


def parse_probe_output(raw: bytes) -> tuple[int, bytes]:
    line, _, rest = raw.partition(b'\n')
    try:
        status = int(line.decode().strip())
    except ValueError:
        status = 0
    return status, rest


def _api_path(raw: str) -> str:
    path = (raw or '').strip() or '/'
    if not path.startswith('/'):
        path = f'/{path}'
    if path.startswith('/api/'):
        return path
    return f'/api{path}'


def _expect_status(raw: Any) -> tuple[int, ...]:
    if isinstance(raw, list) and raw:
        values = [int(item) for item in raw if str(item).isdigit() or isinstance(item, int)]
        if values:
            return tuple(values)
    return (200,)


def _case_from_mapping(item: Mapping[str, Any], *, default_id: str) -> CoreHttpCase | None:
    path = str(item.get('path') or '').strip()
    if not path:
        return None
    method = str(item.get('method') or 'GET').upper()
    json_body = item.get('json')
    body = json_body if isinstance(json_body, dict) else None
    return CoreHttpCase(
        case_id=str(item.get('id') or default_id),
        method=method,
        path=_api_path(path),
        auth=str(item.get('auth') or 'none').lower(),
        expect_status=_expect_status(item.get('expect_status')),
        json_body=body,
    )


def load_core_http_cases(path: Path) -> list[CoreHttpCase]:
    """Все запросы из loadtest/core_scenarios.yaml (scenarios + pages), без дублей."""
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    cases: list[CoreHttpCase] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(data.get('scenarios') or []):
        if not isinstance(item, Mapping):
            continue
        case = _case_from_mapping(item, default_id=f'scenario_{index}')
        if case is None:
            continue
        key = (case.method, case.path, json.dumps(case.json_body, sort_keys=True) if case.json_body else '')
        if key in seen:
            continue
        seen.add(key)
        cases.append(case)
    for page in data.get('pages') or []:
        if not isinstance(page, Mapping):
            continue
        page_id = str(page.get('id') or 'page')
        for index, item in enumerate(page.get('requests') or []):
            if not isinstance(item, Mapping):
                continue
            case = _case_from_mapping(item, default_id=f'{page_id}_{index}')
            if case is None:
                continue
            key = (case.method, case.path, json.dumps(case.json_body, sort_keys=True) if case.json_body else '')
            if key in seen:
                continue
            seen.add(key)
            cases.append(case)
    return cases


def jupyter_probe_paths(port: str, token: str) -> tuple[tuple[str, str], ...]:
    """(ярлык без секрета, url). Сначала /jupyter/lab — режим nginx в Docker."""
    token_q = f'?token={token}' if token else ''
    return (
        (f'/jupyter/lab{token_q and "?token=***"}', f'http://127.0.0.1:{port}/jupyter/lab{token_q}'),
        ('/jupyter/lab', f'http://127.0.0.1:{port}/jupyter/lab'),
        (f'/lab{token_q and "?token=***"}', f'http://127.0.0.1:{port}/lab{token_q}'),
        ('/lab', f'http://127.0.0.1:{port}/lab'),
    )


def http_request_direct(
    url: str,
    *,
    method: str = 'GET',
    headers: Mapping[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 20,
) -> tuple[int, bytes]:
    """HTTP без прокси процесса — как probe-скрипт в контейнере."""
    request = urllib.request.Request(url, method=method.upper())
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if json_body is not None:
        request.data = json.dumps(json_body).encode('utf-8')
        request.add_header('Content-Type', 'application/json')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read() if exc.fp else b''
    except (TimeoutError, OSError, urllib.error.URLError):
        return 0, b''


def http_get(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 20,
) -> tuple[int, bytes]:
    return http_request_direct(url, method='GET', headers=headers, timeout=timeout)


def wait_status(
    url: str,
    *,
    ok: set[int],
    attempts: int = 40,
    delay: float = 3.0,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, bytes]:
    import time

    last_code = 0
    last_body = b''
    for _ in range(max(1, attempts)):
        try:
            last_code, last_body = http_get(url, headers=headers, timeout=12)
        except (TimeoutError, OSError, urllib.error.URLError):
            last_code = 0
            last_body = b''
        if last_code in ok:
            return last_code, last_body
        time.sleep(delay)
    return last_code, last_body


def extract_asset_paths(html: bytes) -> list[str]:
    text = html.decode('utf-8', errors='replace')
    found = re.findall(r'(?:src|href)="(/assets/[^"]+)"', text)
    extra = re.findall(r'(?:src|href)="(/(?:index|LoginPage)[^"]+\.js)"', text)
    paths = list(dict.fromkeys(found + extra))
    return paths


def save_http_dump(directory: Path, name: str, code: int, body: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{name}.status').write_text(str(code), encoding='utf-8')
    (directory / f'{name}.body').write_bytes(body[:65536])


def parse_wget_status(text: str) -> int:
    status = 0
    for line in text.splitlines():
        stripped = line.strip()
        marker = 'HTTP/'
        if marker not in stripped:
            continue
        parts = stripped[stripped.find(marker):].split()
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])
    return status


def parse_ready_json(body: bytes) -> bool:
    try:
        data = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return bool(isinstance(data, dict) and data.get('ready') is True)
