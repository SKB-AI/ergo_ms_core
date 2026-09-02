"""Дымовой набор команд ergoms в изолированном дереве."""

from __future__ import annotations

from service_names import ServiceNames

from ..environment import IsolatedEnvironment
from ..report import CaseResult
from .base import SystemCase

_ALWAYS = (
    ('help', 180),
    ('db-migrate', 600),
    ('security-check', 180),
    ('status', 180),
)


class CommandsSmokeCase(SystemCase):
    name = 'commands_smoke'
    domain = 'commands'

    def run(self, env: IsolatedEnvironment) -> CaseResult:
        failed: list[str] = []
        ran: list[str] = []
        commands = list(_ALWAYS)
        if env.kind == 'docker':
            commands = [
                ('help', 180),
                ('docker-migrate', 600),
                ('security-check', 180),
                ('status', 180),
            ]
        for command, timeout in commands:
            result = env.run_ergoms(command, timeout=timeout)
            ran.append(command)
            allowed = (0, 1) if command == 'security-check' else (0,)
            if result.returncode not in allowed:
                tail = ((result.stderr or '') + (result.stdout or '')).strip().replace('\n', ' ')[-160:]
                failed.append(
                    f'{command}:{result.returncode}:{tail}' if tail else f'{command}:{result.returncode}'
                )
        log_name = ServiceNames(env.prefix).api_dev
        log_result = env.run_ergoms('logs', log_name, '10', timeout=180)
        ran.append('logs')
        if log_result.returncode != 0 and env.kind == 'os-services':
            failed.append(f'logs:{log_result.returncode}')
        extras: list[str] = []
        if env.kind == 'docker':
            extras.append('docker-ps')
        if env.kind == 'os-services':
            extras.extend(('status-all-services', 'test-redis', 'test-nginx'))
        for command in extras:
            result = env.run_ergoms(command, timeout=180)
            ran.append(command)
            if result.returncode != 0:
                failed.append(f'{command}:{result.returncode}')
        if failed:
            return CaseResult(self.name, self.domain, 'fail', ', '.join(failed))
        return CaseResult(self.name, self.domain, 'ok', ', '.join(ran))
