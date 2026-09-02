"""Реестр рецептов pipeline."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402

from lifecycle.context import DeploymentTarget
from lifecycle.pipeline import ParallelStepGroup
from lifecycle.steps.base import DeploymentStep
from lifecycle.steps.common_steps import (
    ClientBuildStep,
    CollectStaticStep,
    EnsureApiSecretStep,
    MigrateStep,
    NpmInstallStep,
    PythonInstallStep,
    WarmupCachesStep,
)
from lifecycle.steps.compose_steps import (
    DockerComposeCommandStep,
    DockerComposeRestartStep,
    DockerComposeUpStep,
)
from lifecycle.steps.dev_steps import DevForegroundStep, DevWorkerStep
from lifecycle.steps.docker_steps import (
    ClearSetupMarkerStep,
    ComposeArtifactsStep,
    DockerBootstrapInfraStep,
    DockerBuildStep,
    DockerMarkSetupStep,
    DockerModulesIgnoreStep,
    DockerStopBeforeBootstrapStep,
    DockerUpAppServicesStep,
    GenerateWorkersComposeStep,
)
from lifecycle.steps.host_steps import (
    ConfigScaffoldStep,
    CreateVenvStep,
    RestoreArtifactOwnershipStep,
    EnsurePortableNodejsStep,
    EnsurePortablePythonStep,
    GitSubmoduleUpdateStep,
    HostCliInstallStep,
    HostExecutionPolicyStep,
    PoetryInstallStep,
    UpdateModuleSubmodulesStep,
)
from lifecycle.steps.infra_steps import (  # noqa: E402
    EnsureMeilisearchOsServiceStep,
    EnsureMeilisearchStep,
    EnsureNginxOsServiceStep,
    EnsureNginxStep,
    EnsurePostgresOsServiceStep,
    EnsureRedisOsServiceStep,
    EnsureRedisStep,
    InfraOperationStep,
    StopSetupStartedInfraStep,
)
from lifecycle.steps.host_lifecycle_steps import ModuleHostServicesStep
from lifecycle.steps.huggingface_steps import PullHuggingfaceModelsStep
from lifecycle.steps.module_tasks_steps import (
    ModuleSetupTasksAfterMigrateStep,
    ModuleSetupTasksStep,
)
from lifecycle.steps.postgres_steps import EnsurePostgresStep
from lifecycle.steps.service_steps import ServiceOperationStep


@dataclass(frozen=True)
class RecipeSpec:
    name: str
    steps: tuple[DeploymentStep, ...]
    target: DeploymentTarget = 'deployment'
    runtime: str = 'host'
    needs_sudo: bool = False
    description: str = ''


def _deploy_api_submodules() -> GitSubmoduleUpdateStep:
    return GitSubmoduleUpdateStep(paths=('core/api', 'core/media_api'))


def _deploy_client_submodules() -> GitSubmoduleUpdateStep:
    return GitSubmoduleUpdateStep(paths=('core/client',))


def _deploy_all_submodules() -> GitSubmoduleUpdateStep:
    return GitSubmoduleUpdateStep(paths=('core/api', 'core/client', 'core/media_api'))


def build_recipe_registry() -> dict[str, RecipeSpec]:
    specs: list[RecipeSpec] = [
        RecipeSpec(
            'setup-full',
            (
                HostExecutionPolicyStep(),
                GitSubmoduleUpdateStep(remote=False),
                ConfigScaffoldStep(),
                RestoreArtifactOwnershipStep(),
                EnsureApiSecretStep(),
                ParallelStepGroup(
                    EnsurePortablePythonStep(),
                    EnsurePortableNodejsStep(),
                    name='portable_runtimes',
                ),
                CreateVenvStep(),
                PoetryInstallStep(),
                HostCliInstallStep(),
                ParallelStepGroup(
                    PythonInstallStep(),
                    NpmInstallStep(),
                    name='python_and_npm',
                ),
                PullHuggingfaceModelsStep(),
                EnsurePostgresStep(),
                EnsurePostgresOsServiceStep(),
                EnsureRedisStep(),
                EnsureMeilisearchStep(),
                EnsureNginxStep(),
                ClientBuildStep(),
                # До migrate: модульные portable (pgvector и т.п.) должны быть в БД до CREATE EXTENSION.
                ModuleSetupTasksStep(),
                MigrateStep(),
                # После migrate: задачи, которым нужна схема БД (RAG sync и т.п.).
                ModuleSetupTasksAfterMigrateStep(),
                WarmupCachesStep(if_needed=True),
                CollectStaticStep(),
                # finally: остановить nginx/redis/модульные демоны и при ошибке посередине.
                StopSetupStartedInfraStep(),
            ),
            description=t('recipe_setup_full'),
        ),
        RecipeSpec(
            'install-python-runtime',
            (EnsurePortablePythonStep(respect_env=False),),
            description=t('recipe_install_python_runtime'),
        ),
        RecipeSpec(
            'install-nodejs',
            (EnsurePortableNodejsStep(respect_env=False),),
            description=t('recipe_install_nodejs'),
        ),
        RecipeSpec(
            'install-deps',
            (
                ParallelStepGroup(
                    PythonInstallStep(),
                    NpmInstallStep(),
                    name='python_and_npm',
                ),
                MigrateStep(),
                WarmupCachesStep(),
            ),
            description=t('recipe_install_deps'),
        ),
        RecipeSpec(
            'python-install',
            (
                CreateVenvStep(),
                PoetryInstallStep(),
                PythonInstallStep(),
            ),
            description=t('recipe_python_install'),
        ),
        RecipeSpec(
            'setup-application',
            (
                ParallelStepGroup(
                    PythonInstallStep(),
                    NpmInstallStep(),
                    name='python_and_npm',
                ),
                ClientBuildStep(),
                MigrateStep(),
                WarmupCachesStep(),
                CollectStaticStep(),
            ),
            description=t('recipe_setup_application'),
        ),
        RecipeSpec(
            'deploy-api',
            (
                _deploy_api_submodules(),
                PythonInstallStep(),
                MigrateStep(),
                WarmupCachesStep(),
                CollectStaticStep(),
            ),
            description=t('recipe_deploy_api'),
        ),
        RecipeSpec(
            'deploy-client',
            (_deploy_client_submodules(), NpmInstallStep(), ClientBuildStep()),
            description=t('recipe_deploy_client'),
        ),
        RecipeSpec(
            'deploy-all',
            (
                _deploy_all_submodules(),
                ParallelStepGroup(
                    PythonInstallStep(),
                    NpmInstallStep(),
                    name='python_and_npm',
                ),
                MigrateStep(),
                WarmupCachesStep(),
                CollectStaticStep(),
                ClientBuildStep(),
            ),
            description=t('recipe_deploy_all'),
        ),
        RecipeSpec(
            'build-all',
            (ClientBuildStep(), CollectStaticStep()),
            description=t('recipe_build_all'),
        ),
        RecipeSpec(
            'update-submodules',
            (GitSubmoduleUpdateStep(),),
            target='aux',
            description=t('recipe_update_submodules'),
        ),
        RecipeSpec(
            'update-module-submodules',
            (UpdateModuleSubmodulesStep(),),
            target='aux',
            description=t('recipe_update_module_submodules'),
        ),
        RecipeSpec(
            'docker-init',
            (
                ClearSetupMarkerStep(),
                DockerModulesIgnoreStep(),
                EnsureApiSecretStep(),
                DockerBuildStep(skip_if_present=True),
                GenerateWorkersComposeStep(),
                ComposeArtifactsStep(),
                DockerStopBeforeBootstrapStep(),
                DockerBootstrapInfraStep(),
                PythonInstallStep(),
                NpmInstallStep(),
                MigrateStep(),
                WarmupCachesStep(),
                DockerMarkSetupStep(),
                DockerUpAppServicesStep(),
            ),
            target='compose',
            runtime='docker',
            description=t('recipe_docker_init'),
        ),
        RecipeSpec(
            'docker-bootstrap',
            (
                EnsureApiSecretStep(),
                DockerStopBeforeBootstrapStep(),
                DockerBootstrapInfraStep(),
                PythonInstallStep(),
                NpmInstallStep(),
                MigrateStep(),
                WarmupCachesStep(),
                DockerMarkSetupStep(),
                DockerUpAppServicesStep(),
            ),
            target='compose',
            runtime='docker',
            description=t('recipe_docker_bootstrap'),
        ),
        RecipeSpec(
            'docker-migrate',
            (MigrateStep(), WarmupCachesStep()),
            target='compose',
            runtime='docker',
            description=t('recipe_docker_migrate'),
        ),
        RecipeSpec(
            'docker-install-deps',
            (PythonInstallStep(),),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'docker-install-npm',
            (NpmInstallStep(),),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'docker-prepare-build',
            (
                EnsureApiSecretStep(),
                DockerModulesIgnoreStep(),
                GenerateWorkersComposeStep(),
                ComposeArtifactsStep(),
            ),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'docker-up',
            (DockerComposeUpStep(),),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'docker-down',
            (DockerComposeCommandStep('down'),),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'docker-ps',
            (DockerComposeCommandStep('ps'),),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'docker-build',
            (
                EnsureApiSecretStep(),
                DockerModulesIgnoreStep(),
                GenerateWorkersComposeStep(),
                ComposeArtifactsStep(resolve_app_ports=False, warn_image_bases=True),
                DockerBuildStep(skip_if_present=False),
            ),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'docker-restart',
            (DockerComposeRestartStep(),),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'docker-logs',
            (DockerComposeCommandStep('logs'),),
            target='compose',
            runtime='docker',
        ),
        RecipeSpec(
            'warmup-caches-if-needed',
            (DevForegroundStep('warmup-caches-if-needed'),),
            target='foreground',
            description=t('recipe_warmup_caches'),
        ),
        RecipeSpec(
            'sync-logs-services',
            (DevForegroundStep('sync-logs-services'),),
            target='aux',
        ),
        RecipeSpec(
            'dev-api',
            (DevForegroundStep('dev-api'),),
            target='foreground',
        ),
        RecipeSpec(
            'dev-client',
            (DevForegroundStep('dev-client'),),
            target='foreground',
        ),
        RecipeSpec(
            'start-client',
            (DevForegroundStep('dev-client'),),
            target='foreground',
        ),
        RecipeSpec(
            'start-client-dev',
            (DevForegroundStep('dev-client-enabled'),),
            target='foreground',
        ),
        RecipeSpec(
            'start-media',
            (DevForegroundStep('dev-media'),),
            target='foreground',
        ),
        RecipeSpec(
            'start-beat',
            (DevForegroundStep('dev-beat'),),
            target='foreground',
        ),
        RecipeSpec(
            'start-jupyter',
            (DevForegroundStep('dev-jupyter'),),
            target='foreground',
        ),
        RecipeSpec(
            'start-worker',
            (DevWorkerStep(),),
            target='foreground',
        ),
    ]

    for op in ('install', 'start', 'stop', 'restart', 'status', 'uninstall'):
        for sid in ('all', 'api', 'client', 'media', 'beat', 'workers'):
            if op == 'uninstall' and sid != 'all':
                continue
            name = f'service-{op}-{sid}'
            if name == 'service-install-all':
                # Postgres/Redis/Meili → app-службы → модули (host_lifecycle) → nginx
                specs.append(
                    RecipeSpec(
                        name,
                        (
                            EnsurePostgresOsServiceStep(),
                            EnsureRedisOsServiceStep(),
                            EnsureMeilisearchOsServiceStep(),
                            ServiceOperationStep(op, sid),
                            ModuleHostServicesStep('install'),
                            EnsureNginxOsServiceStep(),
                        ),
                        target='service',
                        needs_sudo=True,
                        description=t('recipe_service_install_all'),
                    )
                )
                continue
            if name == 'service-uninstall-all':
                # Модули сначала (явный uninstall), затем службы ядра
                specs.append(
                    RecipeSpec(
                        name,
                        (
                            ModuleHostServicesStep('uninstall'),
                            ServiceOperationStep(op, sid),
                        ),
                        target='service',
                        needs_sudo=True,
                        description=t('recipe_service_uninstall_all'),
                    )
                )
                continue
            specs.append(
                RecipeSpec(
                    name,
                    (ServiceOperationStep(op, sid),),
                    target='service',
                    description=t('recipe_service_op', op=op, sid=sid),
                )
            )

    for component, op in (
        ('nginx', 'install'),
        ('nginx', 'install-service'),
        ('nginx', 'uninstall'),
        ('nginx', 'start'),
        ('nginx', 'stop'),
        ('nginx', 'restart'),
        ('nginx', 'reload'),
        ('nginx', 'status'),
        ('nginx', 'test'),
        ('redis', 'install'),
        ('redis', 'install-service'),
        ('redis', 'uninstall'),
        ('redis', 'start'),
        ('redis', 'stop'),
        ('redis', 'restart'),
        ('redis', 'status'),
        ('redis', 'test'),
        ('postgres', 'install'),
        ('postgres', 'install-service'),
        ('postgres', 'uninstall'),
        ('postgres', 'start'),
        ('postgres', 'stop'),
        ('postgres', 'restart'),
        ('postgres', 'status'),
        ('postgres', 'test'),
        ('postgres', 'migrate-to-portable'),
        ('meilisearch', 'install'),
        ('meilisearch', 'install-service'),
        ('meilisearch', 'uninstall'),
        ('meilisearch', 'start'),
        ('meilisearch', 'stop'),
        ('meilisearch', 'restart'),
        ('meilisearch', 'status'),
        ('meilisearch', 'test'),
        ('tls', 'install'),
        ('tls', 'renew'),
        ('tls', 'status'),
    ):
        name = f'{component}-{op}'
        specs.append(
            RecipeSpec(
                name,
                (InfraOperationStep(component, op),),
                target='infra',
                needs_sudo=(
                    component in ('nginx', 'redis', 'postgres', 'tls', 'meilisearch')
                    and op not in ('status', 'test', 'migrate-to-portable')
                ),
                description=t('recipe_infra_op', component=component, op=op),
            )
        )

    registry = {spec.name: spec for spec in specs}

    aliases = {
        'install-services': 'service-install-all',
        'install-api-service': 'service-install-api',
        'install-client-service': 'service-install-client',
        'install-media-service': 'service-install-media',
        'install-beat-service': 'service-install-beat',
        'install-worker-service': 'service-install-workers',
        'start': 'service-start-all',
        'stop': 'service-stop-all',
        'restart': 'service-restart-all',
        'status': 'service-status-all',
        'uninstall-services': 'service-uninstall-all',
        'start-all-services': 'service-start-all',
        'stop-all-services': 'service-stop-all',
        'restart-all-services': 'service-restart-all',
        'status-all-services': 'service-status-all',
        'dev': 'dev-api',
        'install-nginx': 'nginx-install',
        'install-nginx-service': 'nginx-install-service',
        'uninstall-nginx': 'nginx-uninstall',
        'start-nginx': 'nginx-start',
        'stop-nginx': 'nginx-stop',
        'restart-nginx': 'nginx-restart',
        'reload-nginx': 'nginx-reload',
        'status-nginx': 'nginx-status',
        'test-nginx': 'nginx-test',
        'install-redis': 'redis-install',
        'install-redis-service': 'redis-install-service',
        'uninstall-redis': 'redis-uninstall',
        'start-redis': 'redis-start',
        'stop-redis': 'redis-stop',
        'restart-redis': 'redis-restart',
        'status-redis': 'redis-status',
        'test-redis': 'redis-test',
        'install-meilisearch': 'meilisearch-install',
        'install-meilisearch-service': 'meilisearch-install-service',
        'uninstall-meilisearch': 'meilisearch-uninstall',
        'start-meilisearch': 'meilisearch-start',
        'stop-meilisearch': 'meilisearch-stop',
        'restart-meilisearch': 'meilisearch-restart',
        'status-meilisearch': 'meilisearch-status',
        'test-meilisearch': 'meilisearch-test',
        'install-postgres': 'postgres-install',
        'install-postgres-service': 'postgres-install-service',
        'uninstall-postgres': 'postgres-uninstall',
        'start-postgres': 'postgres-start',
        'stop-postgres': 'postgres-stop',
        'restart-postgres': 'postgres-restart',
        'status-postgres': 'postgres-status',
        'test-postgres': 'postgres-test',
        'migrate-postgres-to-portable': 'postgres-migrate-to-portable',
        'install-tls': 'tls-install',
        'renew-tls': 'tls-renew',
        'status-tls': 'tls-status',
        'update-submodules': 'update-submodules',
        'install-python': 'install-python-runtime',
        'install-node': 'install-nodejs',
    }
    for alias, target in aliases.items():
        if target in registry and alias not in registry:
            registry[alias] = registry[target]

    return registry


RECIPE_REGISTRY: dict[str, RecipeSpec] = build_recipe_registry()
