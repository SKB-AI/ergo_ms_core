"""Шаги setup-full: модульные задачи из vscode.tasks.yaml (include_in)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _DEPLOYMENT_DIR / 'scripts'
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from lifecycle.context import DeploymentContext  # noqa: E402
from lifecycle.host import ops as host_ops  # noqa: E402
from lifecycle.steps.base import DeploymentStep, StepResult  # noqa: E402

_MODULE_TASKS_LOADER = _SCRIPTS_DIR / 'module_tasks_loader.py'
_INCLUDE_SETUP_FULL = 'setup-full'
_INCLUDE_SETUP_FULL_AFTER_MIGRATE = 'setup-full-after-migrate'


class _ModuleTasksStepBase(DeploymentStep):
    """Общий запуск задач модулей с заданным include_in (только host)."""

    include_target: str = _INCLUDE_SETUP_FULL
    step_name: str = 'module_setup_tasks'
    running_message_key: str = 'module_setup_tasks_running'
    empty_message_key: str = 'no_module_setup_tasks'

    @property
    def name(self) -> str:
        return self.step_name

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        if not host_ops.venv_exists(ctx.project_root, ctx.platform):
            venv_py = host_ops.venv_python_exe(ctx.project_root, ctx.platform)
            print(format_console('error', t('venv_not_found_at', path=venv_py)), file=sys.stderr)
            return StepResult(exit_code=1, message=t('venv_not_found_msg'))

        tasks = self._load_tasks(ctx)
        if tasks is None:
            return StepResult(exit_code=1, message=t('module_tasks_loader_failed'))
        if not tasks:
            print(format_console('skip', t(self.empty_message_key)))
            return StepResult()

        print(
            format_console(
                'info',
                t(self.running_message_key, count=len(tasks)),
            )
        )
        env = host_ops.api_env(ctx)
        bin_dir = ctx.project_root / 'core' / 'deployment' / 'bin'
        if bin_dir.is_dir():
            sep = ';' if sys.platform == 'win32' else ':'
            existing = env.get('PATH', '')
            env['PATH'] = f'{bin_dir}{sep}{existing}' if existing else str(bin_dir)

        for entry in tasks:
            label = entry.get('label', '')
            command = entry.get('command', '')
            print(format_console('info', f'{label}: {command}'))
            code = subprocess.call(
                command,
                shell=True,
                cwd=str(ctx.project_root),
                env=env,
            )
            if code != 0:
                if entry.get('optional'):
                    print(
                        format_console(
                            'warning',
                            t('module_task_optional_failed', label=label, code=code),
                        )
                    )
                    continue
                print(
                    format_console(
                        'error',
                        t('module_task_failed', label=label, code=code),
                    ),
                    file=sys.stderr,
                )
                return StepResult(
                    exit_code=code,
                    message=t('module_task_exec_failed', command=command),
                )
            print(format_console('ok', label))

        return StepResult()

    def _load_tasks(self, ctx: DeploymentContext) -> list[dict] | None:
        """Задачи из vscode.tasks.yaml через venv (PyYAML недоступен в portable Python)."""
        venv_py = host_ops.venv_python_exe(ctx.project_root, ctx.platform)
        result = subprocess.run(
            [
                str(venv_py), str(_MODULE_TASKS_LOADER),
                '--root', str(ctx.project_root),
                '--json', '--target', self.include_target,
            ],
            cwd=str(ctx.project_root),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            env={
                **os.environ,
                'PYTHONIOENCODING': 'utf-8',
                'PYTHONUTF8': '1',
            },
        )
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr, file=sys.stderr, end='')
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            print(format_console('error', t('module_tasks_loader_failed')), file=sys.stderr)
            return None
        tasks = payload.get('tasks')
        return tasks if isinstance(tasks, list) else None


class ModuleSetupTasksStep(_ModuleTasksStepBase):
    """Задачи include_in: setup-full — до MigrateStep (portable, CREATE EXTENSION)."""

    include_target = _INCLUDE_SETUP_FULL
    step_name = 'module_setup_tasks'
    running_message_key = 'module_setup_tasks_running'
    empty_message_key = 'no_module_setup_tasks'


class ModuleSetupTasksAfterMigrateStep(_ModuleTasksStepBase):
    """Задачи include_in: setup-full-after-migrate — после MigrateStep (схема БД готова)."""

    include_target = _INCLUDE_SETUP_FULL_AFTER_MIGRATE
    step_name = 'module_setup_tasks_after_migrate'
    running_message_key = 'module_setup_tasks_after_migrate_running'
    empty_message_key = 'no_module_setup_tasks_after_migrate'
