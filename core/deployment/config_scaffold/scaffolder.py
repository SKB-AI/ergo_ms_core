"""Оркестратор создания конфигурационных файлов из example-шаблонов."""

from __future__ import annotations

import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t
from console_tags import format_console

from .models import ConfigTemplate, ConfigTemplateRegistry, ScaffoldAction, ScaffoldResult


class ConfigScaffolder:
    """Создаёт рабочие конфиги из example-файлов.

    По умолчанию существующие файлы не перезаписывает. ``overwrite=True`` —
    замена содержимым шаблона (только по явной команде). Для ``.env``
    стратегия сброса возвращает уже заданные секреты после копирования.
    У ``databases.yaml`` уже заданные user/password возвращаются после
    копирования шаблона; секции с секретами, которых нет в минимальном
    наборе, дописываются обратно. При ``ERGO_BROKER=redis`` в существующий
    файл дописывается секция redis, если её ещё нет.
    """

    def __init__(
        self,
        project_root: Path,
        templates: list[ConfigTemplate] | None = None,
    ) -> None:
        self._project_root = project_root.resolve()
        self._templates = templates or ConfigTemplateRegistry.all_templates(self._project_root)

    def run(self, *, overwrite: bool = False) -> list[ScaffoldResult]:
        return [
            template.execute(self._project_root, overwrite=overwrite)
            for template in self._templates
        ]


def format_scaffold_result(result: ScaffoldResult) -> str:
    target = result.display_target

    if result.action is ScaffoldAction.CREATED:
        suffix = f' ({result.detail})' if result.detail else ''
        return t('scaffold_created', target=target, suffix=suffix)

    if result.action is ScaffoldAction.OVERWRITTEN:
        suffix = f' ({result.detail})' if result.detail else ''
        return t('scaffold_overwritten', target=target, suffix=suffix)

    if result.action is ScaffoldAction.SKIPPED_EXISTS:
        suffix = result.detail or ''
        return t('scaffold_exists_skip', target=target, suffix=suffix)

    if result.action is ScaffoldAction.SKIPPED_NO_SOURCE:
        return f'    {format_console("warning", t("scaffold_example_missing", source_rel=result.source_rel))}'

    return f'    {format_console("warning", t("scaffold_create_failed", target=target, detail=result.detail))}'
