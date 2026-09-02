"""Bearer-заголовок для Locust HttpSession."""

from __future__ import annotations

from typing import Any


def apply_bearer(client: Any, access: str) -> None:
    client.headers['Authorization'] = f'Bearer {access}'
