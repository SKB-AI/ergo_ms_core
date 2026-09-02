"""Результаты системного прогона."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CaseResult:
    name: str
    domain: str
    status: str
    detail: str = ''
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in ('ok', 'skip')


@dataclass
class SuiteReport:
    results: list[CaseResult] = field(default_factory=list)

    def add(self, result: CaseResult) -> None:
        self.results.append(result)

    @property
    def failed(self) -> list[CaseResult]:
        return [item for item in self.results if item.status == 'fail']

    @property
    def skipped(self) -> list[CaseResult]:
        return [item for item in self.results if item.status == 'skip']

    def exit_code(self) -> int:
        return 1 if self.failed else 0
