"""Проверка установки с нуля."""

from __future__ import annotations

from ..environment import IsolatedEnvironment, venv_python
from ..report import CaseResult
from .base import SystemCase


class InstallFromScratchCase(SystemCase):
    name = 'install_from_scratch'
    domain = 'install'

    def run(self, env: IsolatedEnvironment) -> CaseResult:
        if env.kind == 'docker':
            env_file = env.tree_root / '.env'
            if not env_file.is_file():
                return CaseResult(self.name, self.domain, 'fail', 'нет throwaway .env')
            text = env_file.read_text(encoding='utf-8')
            if f'DOCKER_COMPOSE_PROJECT={env.prefix}' not in text:
                return CaseResult(self.name, self.domain, 'fail', 'нет отдельного compose-проекта')
            return CaseResult(self.name, self.domain, 'ok', f'compose={env.prefix}')
        python = venv_python(env.tree_root)
        if not python.is_file():
            return CaseResult(self.name, self.domain, 'fail', f'нет venv: {python}')
        env_file = env.tree_root / '.env'
        if not env_file.is_file():
            return CaseResult(self.name, self.domain, 'fail', 'нет throwaway .env')
        return CaseResult(self.name, self.domain, 'ok', f'venv={python}')
