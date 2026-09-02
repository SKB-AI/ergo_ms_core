"""Инфраструктура: nginx, redis, tls."""

from __future__ import annotations

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
from lifecycle.host.shell_bridge import invoke_dispatch  # noqa: E402
from lifecycle.steps.base import DeploymentStep, StepResult  # noqa: E402

_HOST_LIFECYCLE_LOADER = _SCRIPTS_DIR / 'host_lifecycle_loader.py'


class InfraOperationStep(DeploymentStep):
    def __init__(self, component: str, operation: str) -> None:
        self._component = component
        self._operation = operation

    @property
    def name(self) -> str:
        return f'infra_{self._component}_{self._operation}'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        extra: list[str] = []
        if self._operation == 'uninstall' and ctx.option_bool('purge'):
            extra.append('--purge')
        if self._operation == 'renew' and ctx.option_bool('dry_run'):
            extra.append('--dry-run')
        if self._component == 'nginx' and self._operation in ('install', 'install-service'):
            extra.extend([
                ctx.option_str('server_name'),
                ctx.option_str('listen_port'),
            ])
        if self._component == 'redis' and self._operation in ('install', 'install-service'):
            port = ctx.option_str('listen_port')
            if port:
                extra.append(port)
        if self._component == 'postgres' and self._operation == 'install':
            port = ctx.option_str('listen_port')
            if port:
                extra.append(port)
            if ctx.option_bool('with_postgres') or ctx.option_bool('no_skip_system'):
                extra.append('--no-skip-system')
        if self._component == 'postgres' and self._operation == 'migrate-to-portable':
            source_port = ctx.option_str('source_port')
            if source_port:
                extra.extend(['--source-port', source_port])
            source_host = ctx.option_str('source_host')
            if source_host:
                extra.extend(['--source-host', source_host])
            source_user = ctx.option_str('source_user')
            if source_user:
                extra.extend(['--source-user', source_user])
            source_password = ctx.option_str('source_password')
            if source_password:
                extra.extend(['--source-password', source_password])
            if ctx.option_bool('force'):
                extra.append('--force')
            if ctx.option_bool('dry_run'):
                extra.append('--dry-run')
        if self._component == 'tls' and self._operation == 'install':
            extra.extend([
                ctx.option_str('domain'),
                ctx.option_str('email'),
            ])
        if self._component == 'postgres' and self._operation == 'migrate-to-portable':
            ctx.options['needs_sudo'] = False
        else:
            ctx.options.setdefault(
                'needs_sudo',
                self._component in ('nginx', 'redis', 'postgres', 'tls', 'meilisearch'),
            )
        code = invoke_dispatch(ctx, self._component, self._operation, *extra)
        return StepResult(exit_code=code)


class EnsureRedisStep(DeploymentStep):
    """При ERGO_BROKER=redis (или REDIS_ENABLED) — установить portable Redis (setup-full)."""

    @property
    def name(self) -> str:
        return 'ensure_redis'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from deployment_env import is_redis_enabled  # noqa: WPS433

        if not is_redis_enabled():
            print(format_console(
                'skip',
                t('redis_skip_broker'),
            ))
            return StepResult()

        from install_redis import is_installed as redis_is_installed  # noqa: WPS433

        if redis_is_installed(ctx.project_root) and not ctx.option_bool('force'):
            from install_redis import ping_redis, render_redis_conf  # noqa: WPS433
            from security.ensure_infra_credentials import ensure_infra_credentials  # noqa: WPS433

            ensure_infra_credentials(ctx.project_root)
            render_redis_conf(ctx.project_root)
            if ping_redis(ctx.project_root):
                print(format_console('skip', t('redis_already_installed_skip')))
                return StepResult()
            print(format_console('info', t('redis_starting_for_setup')))
            return self._start_redis(ctx)

        print(format_console('info', t('installing_redis')))
        ctx.options.setdefault('needs_sudo', True)
        code = invoke_dispatch(ctx, 'redis', 'install')
        if code != 0:
            return StepResult(exit_code=code, message=t('redis_install_failed'))
        print(format_console('ok', t('redis_ready')))
        return StepResult()

    def _start_redis(self, ctx: DeploymentContext) -> StepResult:
        ctx.options.setdefault('needs_sudo', True)
        code = invoke_dispatch(ctx, 'redis', 'start')
        if code != 0:
            return StepResult(exit_code=code, message=t('redis_start_failed'))
        print(format_console('ok', t('redis_ready')))
        return StepResult()


class EnsureNginxStep(DeploymentStep):
    """При ERGO_PROXY=nginx (или NGINX_ENABLED) — установить portable nginx (setup-full)."""

    @property
    def name(self) -> str:
        return 'ensure_nginx'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from deployment_env import is_nginx_enabled  # noqa: WPS433

        if not is_nginx_enabled():
            print(format_console(
                'skip',
                t('nginx_skip_proxy'),
            ))
            return StepResult()

        print(format_console('info', t('installing_nginx')))
        ctx.options.setdefault('needs_sudo', True)
        code = invoke_dispatch(ctx, 'nginx', 'install')
        if code != 0:
            return StepResult(exit_code=code, message=t('nginx_install_failed'))
        print(format_console('ok', t('nginx_ready')))
        return StepResult()


class EnsureMeilisearchStep(DeploymentStep):
    """При ERGO_SEARCH_ENABLED — установить portable Meilisearch (setup-full)."""

    @property
    def name(self) -> str:
        return 'ensure_meilisearch'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from deployment_env import is_search_enabled  # noqa: WPS433

        if not is_search_enabled():
            print(format_console('skip', t('meilisearch_skip_search')))
            return StepResult()

        from install_meilisearch import is_installed as meili_is_installed  # noqa: WPS433

        if meili_is_installed(ctx.project_root) and not ctx.option_bool('force'):
            print(format_console('skip', t('meilisearch_already_installed_skip')))
            return StepResult()

        print(format_console('info', t('installing_meilisearch')))
        code = invoke_dispatch(ctx, 'meilisearch', 'install')
        if code != 0:
            return StepResult(exit_code=code, message=t('meilisearch_install_failed'))
        print(format_console('ok', t('meilisearch_ready')))
        return StepResult()


class EnsureMeilisearchOsServiceStep(DeploymentStep):
    """При ERGO_SEARCH_ENABLED — зарегистрировать Meilisearch как службу ОС (install-services)."""

    @property
    def name(self) -> str:
        return 'ensure_meilisearch_os_service'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from deployment_env import is_search_enabled  # noqa: WPS433

        if not is_search_enabled():
            print(format_console('skip', t('meilisearch_service_skip')))
            return StepResult()

        print(format_console('info', t('installing_meilisearch_service')))
        ctx.options.setdefault('needs_sudo', True)
        code = invoke_dispatch(ctx, 'meilisearch', 'install-service')
        if code != 0:
            return StepResult(exit_code=code, message=t('meilisearch_service_install_failed'))
        print(format_console('ok', t('meilisearch_service_ready')))
        return StepResult()


class EnsurePostgresOsServiceStep(DeploymentStep):
    """При ERGO_DB=portable_postgres — зарегистрировать portable PostgreSQL как службу ОС."""

    @property
    def name(self) -> str:
        return 'ensure_postgres_os_service'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from deployment_env import should_setup_portable_postgres  # noqa: WPS433

        if not should_setup_portable_postgres():
            print(format_console('skip', t('postgres_service_skip')))
            return StepResult()

        print(format_console('info', t('installing_postgres_service')))
        ctx.options.setdefault('needs_sudo', True)
        code = invoke_dispatch(ctx, 'postgres', 'install-service')
        if code != 0:
            return StepResult(exit_code=code, message=t('postgres_service_install_failed'))
        print(format_console('ok', t('postgres_service_ready')))
        return StepResult()


class EnsureRedisOsServiceStep(DeploymentStep):
    """При REDIS_ENABLED=true — зарегистрировать Redis как службу ОС (install-services)."""

    @property
    def name(self) -> str:
        return 'ensure_redis_os_service'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from deployment_env import is_redis_enabled  # noqa: WPS433

        if not is_redis_enabled():
            print(format_console(
                'skip',
                t('redis_service_skip'),
            ))
            return StepResult()

        print(format_console('info', t('installing_redis_service')))
        ctx.options.setdefault('needs_sudo', True)
        code = invoke_dispatch(ctx, 'redis', 'install-service')
        if code != 0:
            return StepResult(exit_code=code, message=t('redis_service_install_failed'))
        print(format_console('ok', t('redis_service_ready')))
        return StepResult()


class EnsureNginxOsServiceStep(DeploymentStep):
    """При NGINX_ENABLED=true — зарегистрировать nginx как службу ОС (install-services)."""

    @property
    def name(self) -> str:
        return 'ensure_nginx_os_service'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from deployment_env import is_nginx_enabled  # noqa: WPS433

        if not is_nginx_enabled():
            print(format_console(
                'skip',
                t('nginx_service_skip'),
            ))
            return StepResult()

        print(format_console('info', t('installing_nginx_service')))
        ctx.options.setdefault('needs_sudo', True)
        code = invoke_dispatch(ctx, 'nginx', 'install-service')
        if code != 0:
            return StepResult(exit_code=code, message=t('nginx_service_install_failed'))
        print(format_console('ok', t('nginx_service_ready')))
        return StepResult()


class StopSetupStartedInfraStep(DeploymentStep):
    """Cleanup setup-full: остановить nginx/redis и демоны модулей (host_lifecycle stop_commands).

    Выполняется в finally пайплайна — и при успехе, и при ошибке посередине.
    Ошибки остановки — warning, код setup не ломают.
    """

    @property
    def name(self) -> str:
        return 'stop_setup_started_infra'

    @property
    def run_as_cleanup(self) -> bool:
        return True

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from deployment_env import is_nginx_enabled, is_redis_enabled  # noqa: WPS433

        print(format_console('info', t('setup_stopping_started_infra')))

        if is_nginx_enabled():
            print(format_console('info', t('stopping_nginx')))
            ctx.options.setdefault('needs_sudo', True)
            code = invoke_dispatch(ctx, 'nginx', 'stop')
            if code != 0:
                print(
                    format_console('warning', t('setup_stop_failed', name='nginx', code=code)),
                    file=sys.stderr,
                )
            else:
                print(format_console('ok', t('setup_stop_ok', name='nginx')))

        if is_redis_enabled():
            print(format_console('info', t('stopping_redis')))
            ctx.options.setdefault('needs_sudo', True)
            code = invoke_dispatch(ctx, 'redis', 'stop')
            if code != 0:
                print(
                    format_console('warning', t('setup_stop_failed', name='redis', code=code)),
                    file=sys.stderr,
                )
            else:
                print(format_console('ok', t('setup_stop_ok', name='redis')))

        self._stop_module_hosts(ctx)
        return StepResult()

    def _stop_module_hosts(self, ctx: DeploymentContext) -> None:
        """stop_commands из host_lifecycle.yaml (subprocess на venv — без PyYAML в portable)."""
        commands = self._load_stop_commands(ctx)
        if commands is None:
            print(
                format_console('warning', t('setup_module_stop_commands_load_failed')),
                file=sys.stderr,
            )
            return
        if not commands:
            print(format_console('skip', t('setup_no_module_stop_commands')))
            return

        print(format_console('info', t('setup_module_stop_commands_running', count=len(commands))))
        env = host_ops.api_env(ctx)
        bin_dir = ctx.project_root / 'core' / 'deployment' / 'bin'
        if bin_dir.is_dir():
            sep = ';' if sys.platform == 'win32' else ':'
            existing = env.get('PATH', '')
            env['PATH'] = f'{bin_dir}{sep}{existing}' if existing else str(bin_dir)

        for cmd in commands:
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
                        'warning',
                        t('setup_stop_failed', name=cmd, code=code),
                    ),
                    file=sys.stderr,
                )
            else:
                print(format_console('ok', shell_cmd))

    def _load_stop_commands(self, ctx: DeploymentContext) -> list[str] | None:
        if not host_ops.venv_exists(ctx.project_root, ctx.platform):
            return []
        # Loader нужен PyYAML; до успешного python-install в venv его нет.
        stamp = ctx.project_root / host_ops.PYTHON_DEPS_STAMP_REL
        if not stamp.is_file():
            return []
        venv_py = host_ops.venv_python_exe(ctx.project_root, ctx.platform)
        result = subprocess.run(
            [
                str(venv_py),
                str(_HOST_LIFECYCLE_LOADER),
                '--root',
                str(ctx.project_root),
                '--stop-commands',
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
            stderr = result.stderr or ''
            if "No module named 'yaml'" in stderr or 'No module named "yaml"' in stderr:
                return []
            if stderr:
                print(stderr, file=sys.stderr, end='')
            return None
        commands: list[str] = []
        for line in result.stdout.splitlines():
            text = line.strip()
            if text:
                commands.append(text)
        return commands
