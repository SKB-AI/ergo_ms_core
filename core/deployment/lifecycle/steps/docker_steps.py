"""Шаги Docker lifecycle (compose, bootstrap, ignore)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[2]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402

from lifecycle.context import DeploymentContext  # noqa: E402
from lifecycle.docker import ignore as docker_ignore  # noqa: E402
from lifecycle.docker import ops as docker_ops  # noqa: E402
from lifecycle.steps.base import DeploymentStep, StepResult  # noqa: E402
from lifecycle.steps.common_steps import (  # noqa: E402
    ClientBuildStep,
    CollectStaticStep,
    MigrateStep,
    NpmInstallStep,
    PythonInstallStep,
    WarmupCachesStep,
)

_DOCKER_DIR = _DEPLOYMENT_DIR / 'docker'
if str(_DOCKER_DIR) not in sys.path:
    sys.path.insert(0, str(_DOCKER_DIR))

from docker_runtime import prepare_compose_artifacts  # noqa: E402

__all__ = [
    'ClearSetupMarkerStep',
    'ClientBuildStep',
    'CollectStaticStep',
    'ComposeArtifactsStep',
    'DockerBootstrapInfraStep',
    'DockerBuildStep',
    'DockerMarkSetupStep',
    'DockerModulesIgnoreStep',
    'DockerStopBeforeBootstrapStep',
    'DockerUpAppServicesStep',
    'GenerateWorkersComposeStep',
    'MigrateStep',
    'NpmInstallStep',
    'PythonInstallStep',
    'WarmupCachesStep',
]


class ClearSetupMarkerStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'clear_setup_marker'

    def run(self, ctx: DeploymentContext) -> StepResult:
        docker_ops.clear_setup_marker(ctx.project_root)
        return StepResult()


class DockerBuildStep(DeploymentStep):
    def __init__(self, *, skip_if_present: bool = False, extra_args: list[str] | None = None) -> None:
        self._skip_if_present = skip_if_present
        self._extra_args = extra_args or []

    @property
    def name(self) -> str:
        return 'docker_build'

    def run(self, ctx: DeploymentContext) -> StepResult:
        if self._skip_if_present and docker_ops.should_skip_build(ctx.raw_env):
            print(format_console('skip', t('docker_images_already_built')))
            return StepResult()
        extra = (
            list(ctx.options.get('build_extra_args') or [])
            or list(ctx.options.get('compose_extra_args') or [])
            or list(self._extra_args)
        )
        cmd, cwd = docker_ops.build_compose_cmd(
            'build',
            mode=ctx.docker_mode,
            extra_args=extra,
            project_root=ctx.project_root,
        )
        code = subprocess.call(cmd, cwd=str(cwd), env=docker_ops.build_subprocess_env(ctx.raw_env))
        return StepResult(exit_code=code)


class GenerateWorkersComposeStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'generate_workers_compose'

    def run(self, ctx: DeploymentContext) -> StepResult:
        code = docker_ops.run_generate_workers(project_root=ctx.project_root)
        return StepResult(exit_code=code)


class ComposeArtifactsStep(DeploymentStep):
    def __init__(self, *, resolve_app_ports: bool = True, warn_image_bases: bool = False) -> None:
        self._resolve_app_ports = resolve_app_ports
        self._warn_image_bases = warn_image_bases

    @property
    def name(self) -> str:
        return 'compose_artifacts'

    def run(self, ctx: DeploymentContext) -> StepResult:
        prepare_compose_artifacts(
            ctx.project_root,
            resolve_app_ports=self._resolve_app_ports,
            warn_image_bases=self._warn_image_bases,
        )
        return StepResult()


class DockerModulesIgnoreStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'docker_modules_ignore'

    def run(self, ctx: DeploymentContext) -> StepResult:
        docker_ignore.sync_dockerfile_dockerignore(ctx.project_root, ctx.module_catalog)
        return StepResult()


class DockerStopBeforeBootstrapStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'docker_stop_before_bootstrap'

    def run(self, ctx: DeploymentContext) -> StepResult:
        print(format_console('info', t('docker_stop_before_setup')))
        stop_cmd, cwd = docker_ops.build_compose_cmd(
            'stop',
            mode=ctx.docker_mode,
            project_root=ctx.project_root,
        )
        subprocess.call(stop_cmd, cwd=str(cwd))
        return StepResult()


class DockerBootstrapInfraStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'docker_bootstrap_infra'

    def run(self, ctx: DeploymentContext) -> StepResult:
        bootstrap = docker_ops.bootstrap_service_names(ctx.raw_env)
        cmd, cwd = docker_ops.build_compose_cmd(
            'up',
            mode=ctx.docker_mode,
            extra_args=['-d', *bootstrap, *ctx.extra_services],
            project_root=ctx.project_root,
        )
        code = subprocess.call(cmd, cwd=str(cwd))
        if code != 0:
            return StepResult(exit_code=code)
        print(format_console('info', t('waiting_redis_postgres')))
        if not docker_ops.wait_bootstrap_infra(
            ctx.docker_mode,
            ctx.raw_env,
            project_root=ctx.project_root,
        ):
            return StepResult(exit_code=1, message=t('redis_postgres_not_ready'))
        return StepResult()


class DockerMarkSetupStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'mark_setup_complete'

    def run(self, ctx: DeploymentContext) -> StepResult:
        docker_ops.mark_setup_complete(ctx.project_root)
        return StepResult()


class DockerUpAppServicesStep(DeploymentStep):
    @property
    def name(self) -> str:
        return 'docker_up_app_services'

    def run(self, ctx: DeploymentContext) -> StepResult:
        print(format_console('info', t('starting_app_services')))
        from lifecycle.host_profile import (
            SERVICE_API,
            SERVICE_BEAT,
            SERVICE_CLIENT,
            SERVICE_MEDIA,
            resolve_host_profile,
        )

        host = resolve_host_profile(ctx.raw_env)
        services: list[str] = []
        if host.wants(SERVICE_API):
            services.append('api')
        if host.wants(SERVICE_CLIENT) or ctx.resolved_docker_mode() == 'dev':
            services.append('client')
        if host.wants(SERVICE_MEDIA):
            services.append('media-api')
        if host.wants(SERVICE_BEAT):
            services.append('celery-beat')
        for name in ctx.extra_services:
            if name not in services:
                services.append(name)
        cmd, cwd = docker_ops.build_compose_cmd(
            'up',
            mode=ctx.docker_mode,
            extra_args=['-d', *services],
            project_root=ctx.project_root,
        )
        code = subprocess.call(cmd, cwd=str(cwd))
        return StepResult(exit_code=code)

