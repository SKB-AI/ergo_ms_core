"""Создание конфигурационных файлов проекта и модулей из example-шаблонов."""

from .env_compare import (
    EnvCompareResult,
    compare_env_files,
    parse_env_example_lines,
    parse_env_keys,
)
from .models import ConfigTemplate, ConfigTemplateRegistry, EnvFilePair, ScaffoldAction, ScaffoldResult
from .scaffolder import ConfigScaffolder, format_scaffold_result
from .strategies import (
    CopyStrategy,
    DatabasesYamlCopyStrategy,
    EnvPreserveSecretsCopyStrategy,
    FullCopyStrategy,
    HeadLinesCopyStrategy,
    NamedSectionsCopyStrategy,
)

__all__ = [
    'ConfigScaffolder',
    'ConfigTemplate',
    'ConfigTemplateRegistry',
    'CopyStrategy',
    'DatabasesYamlCopyStrategy',
    'EnvCompareResult',
    'EnvFilePair',
    'EnvPreserveSecretsCopyStrategy',
    'FullCopyStrategy',
    'HeadLinesCopyStrategy',
    'NamedSectionsCopyStrategy',
    'ScaffoldAction',
    'ScaffoldResult',
    'compare_env_files',
    'format_scaffold_result',
    'parse_env_example_lines',
    'parse_env_keys',
]
