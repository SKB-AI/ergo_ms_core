"""Структура отчёта и печать через format_console."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal['ok', 'warning', 'error', 'skip', 'info']

_TAG = {
    'ok': 'ok',
    'warning': 'warning',
    'error': 'error',
    'skip': 'skip',
    'info': 'info',
}


@dataclass
class Finding:
    control_id: str
    severity: Severity
    message: str
    title: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'control_id': self.control_id,
            'severity': self.severity,
            'message': self.message,
            'title': self.title,
        }


@dataclass
class Report:
    level: str
    level_source: str
    enforce: str
    deploy_type: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'error')

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'warning')

    @property
    def skip_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'skip')

    def exit_code(self, *, enforce: str) -> int:
        """
        off → всегда 0
        warn → 0 / 1 (warnings) / 2 (errors)
        raise → warning тоже даёт >=1; errors → 2
        """
        mode = (enforce or 'warn').strip().lower()
        if mode == 'off':
            return 0
        if self.error_count:
            return 2
        if self.warning_count:
            return 1 if mode == 'warn' else 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'level': self.level,
            'level_source': self.level_source,
            'enforce': self.enforce,
            'deploy_type': self.deploy_type,
            'error_count': self.error_count,
            'warning_count': self.warning_count,
            'skip_count': self.skip_count,
            'findings': [f.to_dict() for f in self.findings],
        }


def print_finding(finding: Finding, *, format_console, file=None) -> None:
    tag = _TAG.get(finding.severity, 'info')
    label = finding.control_id
    if finding.title:
        label = f'{finding.control_id} — {finding.title}'
    text = f'{label} — {finding.message}' if finding.message else label
    print(format_console(tag, text), file=file)
