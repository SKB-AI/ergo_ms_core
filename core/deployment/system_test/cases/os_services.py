"""Проверка живых служб ОС с тестовым префиксом."""

from __future__ import annotations

from service_names import DEFAULT_PREFIX, ServiceNames

from ..environment import IsolatedEnvironment
from ..http import http_status
from ..report import CaseResult
from .base import SystemCase


class OsServicesInstalledCase(SystemCase):
    name = 'os_services_installed'
    domain = 'os-services'
    environments = ('os-services',)

    def run(self, env: IsolatedEnvironment) -> CaseResult:
        names = ServiceNames(env.prefix)
        if env.prefix == DEFAULT_PREFIX or not env.prefix.startswith('ergo_st_'):
            return CaseResult(self.name, self.domain, 'fail', 'тестовый префикс не задан')
        result = env.run_ergoms('status', timeout=180)
        text = (result.stdout or '') + (result.stderr or '')
        if names.api_dev not in text and result.returncode != 0:
            return CaseResult(
                self.name,
                self.domain,
                'fail',
                f'status не видит {names.api_dev}',
            )
        status = http_status(env.http_base().rstrip('/') + '/api/system/ready/')
        if status == 0:
            return CaseResult(self.name, self.domain, 'fail', 'API службы не отвечает')
        return CaseResult(self.name, self.domain, 'ok', f'{names.api_dev} ready={status}')
