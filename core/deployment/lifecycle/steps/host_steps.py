"""Host-шаги setup (submodules, venv, scaffold, cli)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[2]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402

from lifecycle.context import DeploymentContext, HostPlatform  # noqa: E402
from lifecycle.host import ops as host_ops  # noqa: E402
from lifecycle.host.shell_bridge import invoke_dispatch  # noqa: E402
from lifecycle.steps.base import DeploymentStep, StepResult  # noqa: E402

# Ядро + community-репозиторий организации (шаблоны issues/PR, CONTRIBUTING и т.п.).
# Deploy-рецепты передают paths явно и .github не тянут.
DEFAULT_CORE_SUBMODULES = (
    'core/api',
    'core/client',
    'core/media_api',
    'core/.github',
)


class HostExecutionPolicyStep(DeploymentStep):
    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.platform == HostPlatform.WIN32

    @property
    def name(self) -> str:
        return 'host_execution_policy'

    def run(self, ctx: DeploymentContext) -> StepResult:
        script = (
            '$p = Get-ExecutionPolicy -Scope CurrentUser -ErrorAction SilentlyContinue; '
            'if ($p -ne "RemoteSigned") { '
            'Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force }'
        )
        code = subprocess.call(
            ['powershell.exe', '-NoProfile', '-Command', script],
            cwd=str(ctx.project_root),
        )
        return StepResult(exit_code=code)


def _submodule_initialized(root: Path, rel: str) -> bool:
    """True, если submodule уже клонирован (.git — файл или каталог)."""
    sub = root / rel
    return (sub / '.git').exists() and sub.is_dir()


class GitSubmoduleUpdateStep(DeploymentStep):
    def __init__(
        self,
        paths: tuple[str, ...] = DEFAULT_CORE_SUBMODULES,
        branch: str = 'dev',
        *,
        remote: bool = True,
    ) -> None:
        self._paths = paths
        self._branch = branch
        self._remote = remote

    @property
    def name(self) -> str:
        return 'git_submodule_update'

    def run(self, ctx: DeploymentContext) -> StepResult:
        root = ctx.project_root
        paths = ctx.options.get('submodule_paths', self._paths)
        branch = ctx.option_str('checkout_branch', self._branch)
        force = ctx.option_bool('force')
        already_init = bool(paths) and all(_submodule_initialized(root, rel) for rel in paths)
        # setup-full (remote=False): без --remote; при уже клонированных — полный skip
        use_remote = bool(force or self._remote)
        if not use_remote and already_init and not force:
            print(format_console('skip', t('git_submodules_already_init_skip')))
            return StepResult()
        cmd = ['git', 'submodule', 'update', '--init']
        if use_remote:
            cmd.append('--remote')
        cmd.extend(paths)
        code = subprocess.call(cmd, cwd=str(root))
        if code != 0:
            return StepResult(exit_code=code, message=t('git_submodule_update_failed'))
        for rel in paths:
            sub = root / rel
            if sub.is_dir():
                subprocess.call(['git', 'checkout', branch], cwd=str(sub))
        print(format_console('ok', t('git_submodules_updated')))
        return StepResult()


class ConfigScaffoldStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'config_scaffold'

    def run(self, ctx: DeploymentContext) -> StepResult:
        script = ctx.project_root / 'core' / 'deployment' / 'scripts' / 'scaffold_config_files.py'
        if not script.is_file():
            print(format_console('skip', t('scaffold_script_not_found')))
            return StepResult()
        argv = [*host_ops.base_python_argv(ctx.project_root, ctx.platform), str(script), '--root', str(ctx.project_root)]
        code = subprocess.call(argv, cwd=str(ctx.project_root))
        return StepResult(exit_code=code)


class RestoreArtifactOwnershipStep(DeploymentStep):
    """Кэш Vite, portable-пакеты и логи не должны оставаться у root после sudo."""

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host' and ctx.platform != HostPlatform.WIN32

    @property
    def name(self) -> str:
        return 'restore_artifact_ownership'

    def run(self, ctx: DeploymentContext) -> StepResult:
        from lifecycle.host.privilege import restore_runtime_artifact_ownership

        restore_runtime_artifact_ownership(ctx.project_root)
        return StepResult()


class CreateVenvStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'create_venv'

    def run(self, ctx: DeploymentContext) -> StepResult:
        code = host_ops.create_or_validate_venv(ctx, recreate=ctx.option_bool('recreate_venv'))
        return StepResult(exit_code=code)


class EnsurePortablePythonStep(DeploymentStep):
    def __init__(self, *, respect_env: bool = True) -> None:
        self._respect_env = respect_env

    @property
    def name(self) -> str:
        return 'ensure_portable_python'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        if self._respect_env and not host_ops.portable_python_enabled(ctx):
            print(format_console('skip', t('portable_python_disabled_skip')))
            return StepResult()
        print(format_console('info', t('checking_portable_python')))
        code = host_ops.ensure_portable_python(ctx, force=ctx.option_bool('force_runtime'))
        return StepResult(exit_code=code)


class EnsurePortableNodejsStep(DeploymentStep):
    def __init__(self, *, respect_env: bool = True) -> None:
        self._respect_env = respect_env

    @property
    def name(self) -> str:
        return 'ensure_portable_nodejs'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        if self._respect_env and not host_ops.portable_nodejs_enabled(ctx):
            print(format_console('skip', t('portable_nodejs_disabled_skip')))
            return StepResult()
        print(format_console('info', t('checking_portable_nodejs')))
        code = host_ops.ensure_portable_nodejs(ctx, force=ctx.option_bool('force_runtime'))
        return StepResult(exit_code=code)


class PoetryInstallStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'poetry_install'

    def run(self, ctx: DeploymentContext) -> StepResult:
        code = host_ops.install_poetry_in_venv(ctx)
        return StepResult(exit_code=code)


class HostCliInstallStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'cli_install'

    def run(self, ctx: DeploymentContext) -> StepResult:
        code = invoke_dispatch(ctx, 'cli', 'install')
        return StepResult(exit_code=code)


class UpdateModuleSubmodulesStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'update_module_submodules'

    def run(self, ctx: DeploymentContext) -> StepResult:
        root = ctx.project_root
        gitmodules = root / '.gitmodules'
        if not gitmodules.is_file():
            return StepResult(exit_code=1, message=t('gitmodules_not_found'))
        result = subprocess.run(
            ['git', 'config', '-f', '.gitmodules', '--get-regexp', r'^submodule\..*\.path$'],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        entries: list[tuple[str, str]] = []
        for line in (result.stdout or '').splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue
            key, path = parts[0], parts[1]
            if not path.startswith('modules/'):
                continue
            name = key.removeprefix('submodule.').removesuffix('.path')
            entries.append((name, path))
        if not entries:
            print(format_console('skip', t('module_submodules_not_found')))
            return StepResult()

        succeeded = 0
        skipped = 0
        failed = 0
        failed_paths: list[str] = []

        for name, rel in entries:
            index = subprocess.run(
                ['git', 'ls-files', '-s', '--', rel],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            if not (index.stdout or '').strip():
                print(format_console('skip', t('path_not_in_git_index', rel=rel)))
                skipped += 1
                continue

            branch_result = subprocess.run(
                ['git', 'config', '-f', '.gitmodules', f'submodule.{name}.branch'],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
            branch = (branch_result.stdout or '').strip() or 'dev'

            code = subprocess.call(
                ['git', 'submodule', 'update', '--init', '--remote', '--', rel],
                cwd=str(root),
            )
            if code != 0:
                print(format_console('warning', t('failed_update_rel', rel=rel)), file=sys.stderr)
                failed += 1
                failed_paths.append(rel)
                continue

            sub = root / rel
            if not sub.is_dir():
                print(format_console('warning', t('submodule_dir_not_found', rel=rel)), file=sys.stderr)
                failed += 1
                failed_paths.append(rel)
                continue

            checkout_code = subprocess.call(['git', 'checkout', branch], cwd=str(sub))
            if checkout_code != 0:
                print(
                    format_console('warning', t('failed_checkout_branch', branch=branch, rel=rel)),
                    file=sys.stderr,
                )
            succeeded += 1

        if succeeded > 0:
            summary = t('modules_updated_count', succeeded=succeeded)
            if skipped > 0 or failed > 0:
                summary += t('modules_updated_summary_extra', skipped=skipped, failed=failed)
            print(format_console('ok', summary))
            for rel in failed_paths:
                print(f'  - {rel}', file=sys.stderr)
            return StepResult()

        if failed > 0:
            print(format_console('error', t('failed_update_any_module', failed=failed)), file=sys.stderr)
            for rel in failed_paths:
                print(f'  - {rel}', file=sys.stderr)
            return StepResult(exit_code=1, message=t('failed_update_module_submodules'))

        print(format_console('warning', t('no_modules_to_update')), file=sys.stderr)
        return StepResult()
