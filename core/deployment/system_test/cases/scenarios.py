"""Обёртка над матрицей scenario_test."""

from __future__ import annotations

import re
import subprocess

from scenario_test.matrix import all_specs, filter_specs

from ..environment import IsolatedEnvironment, current_python
from ..report import CaseResult
from .base import SystemCase

AGENT_SPECS = (
    'host_sqlite_direct',
    'host_postgres_redis_nginx',
    'docker_direct',
    'docker_nginx_jupyter',
)


class ScenarioSpecCase(SystemCase):
    domain = 'scenarios'
    environments = ('docker', 'host')

    def __init__(self, spec_id: str) -> None:
        self.spec_id = spec_id
        self.name = f'scenario_{spec_id}'
        spec = next((item for item in all_specs() if item.id == spec_id), None)
        if spec is not None:
            self.environments = (spec.launch,)

    def run(self, env: IsolatedEnvironment) -> CaseResult:
        script = env.workspace / 'core' / 'deployment' / 'scripts' / 'deployment_scenario_test.py'
        result = subprocess.run(
            [str(current_python()), str(script), '--spec', self.spec_id],
            cwd=str(env.workspace),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            timeout=7200,
        )
        text = (result.stderr or '') + '\n' + (result.stdout or '')
        match = re.search(r'result=(\d+)', text)
        code = int(match.group(1)) if match else result.returncode
        if code == 2:
            return CaseResult(self.name, self.domain, 'skip', 'нет Docker / portable / портов / образа')
        if code != 0 or result.returncode != 0:
            tail = text[-1200:]
            return CaseResult(self.name, self.domain, 'fail', tail)
        return CaseResult(self.name, self.domain, 'ok', self.spec_id)


def scenario_cases(*, all_specs_mode: bool, spec_ids: list[str] | None = None) -> list[SystemCase]:
    if spec_ids:
        selected = filter_specs(spec_ids=spec_ids)
        return [ScenarioSpecCase(item.id) for item in selected]
    if all_specs_mode:
        return [ScenarioSpecCase(item.id) for item in all_specs()]
    return [ScenarioSpecCase(item_id) for item_id in AGENT_SPECS]
