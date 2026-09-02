"""Базовый кейс системного теста."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..environment import IsolatedEnvironment
from ..report import CaseResult


class SystemCase(ABC):
    name: str = ''
    domain: str = ''
    environments: tuple[str, ...] = ('docker', 'host', 'os-services')

    def applies_to(self, kind: str) -> bool:
        return kind in self.environments

    @abstractmethod
    def run(self, env: IsolatedEnvironment) -> CaseResult:
        """Проверка на уже поднятом окружении."""
