"""Шаги install/uninstall-services: модульные OS-службы из host_lifecycle.yaml."""

from __future__ import annotations

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

_VALID_OPS = frozenset({'install', 'uninstall'})


class ModuleHostServicesStep(DeploymentStep):
    """Выполнить host.*_service_commands модулей (install или uninstall, только host)."""

    def __init__(self, operation: str) -> None:
        if operation not in _VALID_OPS:
            raise ValueError(f'unsupported host lifecycle operation: {operation}')
        self._operation = operation

    @property
    def name(self) -> str:
        return f'module_host_{self._operation}_services'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        # Ленивый import: избегаем цикла recipes → step → loader → lifecycle
        from host_lifecycle_loader import (  # noqa: WPS433
            aggregate_host_lifecycle,
            collect_stale_module_process_uninstall_commands,
            collect_uninstall_service_commands,
        )

        agg = aggregate_host_lifecycle(ctx.project_root)
        stale: list[str] = []
        skipped_process: list[str] = []
        if self._operation == 'install':
            commands = list(agg.install_service_commands)
            stale = collect_stale_module_process_uninstall_commands(ctx.project_root)
            skipped_process = list(agg.skipped_process_install_commands)
            skip_key = 'no_module_host_install_services'
            running_key = 'module_host_install_services_running'
            failed_key = 'module_host_install_service_failed'
        else:
            yaml_commands = list(agg.uninstall_service_commands)
            commands = collect_uninstall_service_commands(ctx.project_root)
            skip_key = (
                'no_module_host_uninstall_services'
                if not yaml_commands
                else 'module_host_uninstall_nothing_installed'
            )
            running_key = 'module_host_uninstall_services_running'
            failed_key = 'module_host_uninstall_service_failed'

        env = _command_env(ctx)

        if stale:
            print(format_console('info', t('module_process_services_stale_cleanup', count=len(stale))))
            result = _run_host_commands(
                stale,
                ctx,
                env,
                failed_key='module_host_uninstall_service_failed',
            )
            if result.exit_code != 0:
                return result

        if skipped_process:
            print(format_console('skip', t('module_process_services_skip_runtime')))

        if not commands:
            if stale or skipped_process:
                return StepResult()
            print(format_console('skip', t(skip_key)))
            return StepResult()

        print(format_console('info', t(running_key, count=len(commands))))
        return _run_host_commands(commands, ctx, env, failed_key=failed_key)


def _command_env(ctx: DeploymentContext) -> dict:
    env = host_ops.api_env(ctx)
    bin_dir = ctx.project_root / 'core' / 'deployment' / 'bin'
    if bin_dir.is_dir():
        sep = ';' if sys.platform == 'win32' else ':'
        existing = env.get('PATH', '')
        env['PATH'] = f'{bin_dir}{sep}{existing}' if existing else str(bin_dir)
    return env


def _run_host_commands(
    commands: list[str],
    ctx: DeploymentContext,
    env: dict,
    *,
    failed_key: str,
) -> StepResult:
    for cmd in commands:
        # В YAML — «модуль:команда» без префикса ergoms (как в тестах deployment)
        shell_cmd = f'ergoms {cmd}'
        print(format_console('info', shell_cmd))
        code = subprocess.call(
            shell_cmd,
            shell=True,
            cwd=str(ctx.project_root),
            env=env,
        )
        if code != 0:
            print(
                format_console(
                    'error',
                    t(failed_key, command=cmd, code=code),
                ),
                file=sys.stderr,
            )
            return StepResult(
                exit_code=code,
                message=t('module_task_exec_failed', command=shell_cmd),
            )
        print(format_console('ok', shell_cmd))
    return StepResult()


# Совместимость с прежним именем шага install
class ModuleHostInstallServicesStep(ModuleHostServicesStep):
    def __init__(self) -> None:
        super().__init__('install')
