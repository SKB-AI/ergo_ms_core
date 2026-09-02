"""HTTP-хелперы для живых системных кейсов."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Mapping

# Локальный прокси (HTTP_PROXY) не должен перехватывать loopback системного теста.
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_status(
    url: str,
    *,
    timeout: float = 15.0,
    headers: Mapping[str, str] | None = None,
) -> int:
    request = urllib.request.Request(url, method='GET', headers=dict(headers or {}))
    try:
        with _NO_PROXY.open(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except OSError:
        return 0


def http_exchange(
    url: str,
    *,
    timeout: float = 15.0,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(url, method='GET', headers=dict(headers or {}))
    try:
        with _NO_PROXY.open(request, timeout=timeout) as response:
            body = response.read().decode('utf-8', errors='replace')
            hdrs = {key.lower(): value for key, value in response.headers.items()}
            return int(response.status), hdrs, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace') if exc.fp else ''
        hdrs = {key.lower(): value for key, value in exc.headers.items()} if exc.headers else {}
        return int(exc.code), hdrs, body
    except OSError:
        return 0, {}, ''


def wait_http(base_url: str, *, timeout_sec: float = 120.0, path: str = '/api/') -> None:
    deadline = time.monotonic() + timeout_sec
    url = base_url.rstrip('/') + path
    last = 'no attempt'
    while time.monotonic() < deadline:
        status = http_status(url, timeout=10.0)
        if status != 0:
            return
        last = f'status={status}'
        time.sleep(0.5)
    raise RuntimeError(f'нет HTTP на {url} за {timeout_sec:.0f}s ({last})')
