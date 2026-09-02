"""Нагрузочное тестирование API ERGO MS (Locust)."""

from __future__ import annotations

__all__ = ['LOADTEST_DIR']

from pathlib import Path

LOADTEST_DIR = Path(__file__).resolve().parent
