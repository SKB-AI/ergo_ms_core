"""Модели результата и описания шаблона конфигурации."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .strategies import (
    CopyStrategy,
    DatabasesYamlCopyStrategy,
    EnvPreserveSecretsCopyStrategy,
    FullCopyStrategy,
)


class ScaffoldAction(str, Enum):
    CREATED = 'created'
    OVERWRITTEN = 'overwritten'
    SKIPPED_EXISTS = 'skipped_exists'
    SKIPPED_NO_SOURCE = 'skipped_no_source'
    FAILED = 'failed'


@dataclass(frozen=True)
class ScaffoldResult:
    source_rel: str
    target_rel: str
    action: ScaffoldAction
    detail: str = ''

    @property
    def display_target(self) -> str:
        return self.target_rel.replace('\\', '/')


@dataclass(frozen=True)
class EnvFilePair:
    label: str
    example_path: Path
    env_path: Path


@dataclass(frozen=True)
class ConfigTemplate:
    source_rel: str
    target_rel: str
    strategy: CopyStrategy
    created_detail: str = ''

    def execute(self, project_root: Path, *, overwrite: bool = False) -> ScaffoldResult:
        source = project_root / self.source_rel
        target = project_root / self.target_rel
        source_display = self.source_rel.replace('\\', '/')
        target_display = self.target_rel.replace('\\', '/')

        if not source.is_file():
            return ScaffoldResult(source_display, target_display, ScaffoldAction.SKIPPED_NO_SOURCE)

        existed = target.exists()
        if existed and not overwrite:
            try:
                detail = self.strategy.merge_into_existing(source, target)
            except OSError as exc:
                return ScaffoldResult(
                    source_display,
                    target_display,
                    ScaffoldAction.FAILED,
                    str(exc),
                )
            return ScaffoldResult(
                source_display,
                target_display,
                ScaffoldAction.SKIPPED_EXISTS,
                detail,
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self.strategy.copy(source, target)
            detail = self.created_detail
            last_detail = getattr(self.strategy, 'last_detail', '') or ''
            if last_detail:
                detail = last_detail
            action = ScaffoldAction.OVERWRITTEN if existed else ScaffoldAction.CREATED
            return ScaffoldResult(
                source_display,
                target_display,
                action,
                detail,
            )
        except OSError as exc:
            return ScaffoldResult(
                source_display,
                target_display,
                ScaffoldAction.FAILED,
                str(exc),
            )


class ConfigTemplateRegistry:
    """Реестр шаблонов: корень проекта и модульные .env."""

    @staticmethod
    def project_templates(project_root: Path) -> list[ConfigTemplate]:
        # .env раньше databases.yaml — чтобы ERGO_BROKER уже был на диске в том же прогоне.
        return [
            ConfigTemplate(
                '.env.example',
                '.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'databases.yaml.example',
                'databases.yaml',
                DatabasesYamlCopyStrategy(project_root),
            ),
            ConfigTemplate(
                'celery_workers.yaml.example',
                'celery_workers.yaml',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/nginx.env.example',
                'env/nginx.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/docker.env.example',
                'env/docker.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/jupyter.env.example',
                'env/jupyter.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/smtp.env.example',
                'env/smtp.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/logging.env.example',
                'env/logging.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/mcp.env.example',
                'env/mcp.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/media.env.example',
                'env/media.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/realtime.env.example',
                'env/realtime.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/cache.env.example',
                'env/cache.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/celery.env.example',
                'env/celery.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/postgres.env.example',
                'env/postgres.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/search.env.example',
                'env/search.env',
                FullCopyStrategy(),
            ),
            ConfigTemplate(
                'env/modules.env.example',
                'env/modules.env',
                FullCopyStrategy(),
            ),
        ]

    @staticmethod
    def module_env_templates(project_root: Path) -> list[ConfigTemplate]:
        modules_dir = project_root / 'modules'
        if not modules_dir.is_dir():
            return []

        deployment = project_root / 'core' / 'deployment'
        import sys

        if str(deployment) not in sys.path:
            sys.path.insert(0, str(deployment))
        from lifecycle.modules.catalog import ModuleCatalog  # noqa: WPS433

        catalog = ModuleCatalog.from_env(project_root)

        templates: list[ConfigTemplate] = []
        for module_dir in sorted(modules_dir.iterdir()):
            if not module_dir.is_dir() or catalog.is_disabled(module_dir.name):
                continue
            source = module_dir / '.env.example'
            if not source.is_file():
                continue
            templates.append(
                ConfigTemplate(
                    source.relative_to(project_root).as_posix(),
                    (module_dir / '.env').relative_to(project_root).as_posix(),
                    FullCopyStrategy(),
                ),
            )
        return templates

    @classmethod
    def all_templates(cls, project_root: Path) -> list[ConfigTemplate]:
        return cls.project_templates(project_root) + cls.module_env_templates(project_root)

    @classmethod
    def reset_templates(cls, project_root: Path) -> list[ConfigTemplate]:
        """Шаблоны для ``ergoms env --reset-from-example``: .env, фрагменты, модули, yaml.

        Для ``*.env.example`` уже заданные секреты рабочего файла сохраняются.
        """
        templates: list[ConfigTemplate] = []
        for template in cls.all_templates(project_root):
            if template.source_rel.endswith('.env.example'):
                templates.append(
                    ConfigTemplate(
                        template.source_rel,
                        template.target_rel,
                        EnvPreserveSecretsCopyStrategy(),
                        created_detail=template.created_detail,
                    ),
                )
            else:
                templates.append(template)
        return templates

    @classmethod
    def env_templates(cls, project_root: Path) -> list[ConfigTemplate]:
        return [
            template
            for template in cls.all_templates(project_root)
            if template.source_rel.endswith('.env.example')
        ]

    @classmethod
    def env_check_pairs(cls, project_root: Path) -> list[EnvFilePair]:
        pairs: list[EnvFilePair] = []
        for template in cls.env_templates(project_root):
            example_path = project_root / template.source_rel
            env_path = project_root / template.target_rel
            label = template.target_rel.replace('\\', '/')
            pairs.append(
                EnvFilePair(
                    label=label,
                    example_path=example_path,
                    env_path=env_path,
                ),
            )
        return pairs

