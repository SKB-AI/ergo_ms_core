"""
Единый каталог модулей: FS + DISABLED_MODULES + фильтр процесса (microservice).

Используется в deployment lifecycle и через фасад module_registry в API.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import FrozenSet

_SKIPPED_MODULE_DIR_NAMES = frozenset({'__pycache__'})

# Роли ядра, для которых в MODULE_RUNTIME=microservice исключаются MICROSERVICE_MODULES.
# Beat ядра не планирует вынесенные модули: у них свой процесс на хосте модуля.
_CORE_API_PROCESS_ROLES = frozenset({'api', 'core-api'})
_CORE_SCHEDULE_PROCESS_ROLES = frozenset({'api', 'core-api', 'beat'})

# Каноническое значение + устаревший алиас ``split``.
_RUNTIME_MICROSERVICE = frozenset({'microservice', 'split'})


def parse_csv_modules(raw: str = '') -> frozenset[str]:
    return frozenset(m.strip() for m in raw.split(',') if m.strip())


def parse_disabled_modules_raw(raw: str = '') -> frozenset[str]:
    return parse_csv_modules(raw)


def parse_module_runtime(raw: str = '') -> str:
    """``monolith`` или ``microservice`` (алиас ``split`` → microservice)."""
    value = (raw or 'monolith').strip().lower()
    if value in _RUNTIME_MICROSERVICE:
        return 'microservice'
    if value == 'monolith':
        return 'monolith'
    return 'monolith'


def parse_process_role(raw: str = '') -> str:
    return (raw or '').strip().lower()


def parse_microservice_modules(environ: Mapping[str, str]) -> frozenset[str]:
    """MICROSERVICE_MODULES из окружения."""
    raw = environ.get('MICROSERVICE_MODULES', '')
    return parse_csv_modules(raw)


class ModuleCatalog:
    """Реестр модулей на диске с учётом DISABLED_MODULES и роли процесса."""

    def __init__(
        self,
        project_root: Path,
        *,
        disabled: FrozenSet[str] | None = None,
        modules_dir_name: str = 'modules',
        module_runtime: str = 'monolith',
        process_role: str = '',
        microservice_modules: FrozenSet[str] | None = None,
        process_modules: FrozenSet[str] | None = None,
        process_modules_explicit: bool = False,
        colocated_modules: FrozenSet[str] | None = None,
        colocate_enabled: bool = False,
    ) -> None:
        self._project_root = project_root.resolve()
        self._modules_dir = self._project_root / modules_dir_name
        self._disabled = disabled if disabled is not None else frozenset()
        self._module_runtime = parse_module_runtime(module_runtime)
        self._process_role = parse_process_role(process_role)
        self._microservice_modules = (
            microservice_modules if microservice_modules is not None else frozenset()
        )
        self._process_modules = process_modules if process_modules is not None else frozenset()
        self._process_modules_explicit = process_modules_explicit
        self._colocated_modules = (
            colocated_modules if colocated_modules is not None else frozenset()
        )
        self._colocate_enabled = bool(colocate_enabled)

    @classmethod
    def from_env(
        cls,
        project_root: Path,
        environ: Mapping[str, str] | None = None,
    ) -> ModuleCatalog:
        env = environ if environ is not None else os.environ
        process_modules_raw = env.get('PROCESS_MODULES', '')
        from lifecycle.modules.colocate import (  # noqa: WPS433
            colocated_module_names_from_env,
            parse_bridge_colocate,
        )

        colocate_on = (
            parse_bridge_colocate(
                env.get('BRIDGE_COLOCATE', ''),
                transport=env.get('BRIDGE_TRANSPORT', 'local'),
            )
            == 'on'
        )
        return cls(
            project_root,
            disabled=parse_disabled_modules_raw(env.get('DISABLED_MODULES', '')),
            module_runtime=env.get('MODULE_RUNTIME', 'monolith'),
            process_role=env.get('ERGO_PROCESS_ROLE', ''),
            microservice_modules=parse_microservice_modules(env),
            process_modules=parse_csv_modules(process_modules_raw),
            process_modules_explicit=bool(process_modules_raw.strip()),
            colocated_modules=colocated_module_names_from_env(env),
            colocate_enabled=colocate_on,
        )

    @classmethod
    def from_project_env(
        cls,
        project_root: Path,
        environ: Mapping[str, str] | None = None,
    ) -> ModuleCatalog:
        """Как ``from_env``, но сначала читает корневой ``.env`` и ``env/*.env``."""
        from env_file_loader import load_project_env  # noqa: WPS433

        values = dict(load_project_env(project_root))
        overlay = environ if environ is not None else os.environ
        for key, val in overlay.items():
            if val is not None and str(val).strip() != '':
                values[key] = str(val).strip()
        return cls.from_env(project_root, values)

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def modules_dir(self) -> Path:
        return self._modules_dir

    @property
    def disabled(self) -> FrozenSet[str]:
        return self._disabled

    @property
    def module_runtime(self) -> str:
        return self._module_runtime

    @property
    def process_role(self) -> str:
        return self._process_role

    @property
    def microservice_modules(self) -> FrozenSet[str]:
        return self._microservice_modules

    @property
    def split_modules(self) -> FrozenSet[str]:
        """Устаревший алиас ``microservice_modules``."""
        return self._microservice_modules

    @property
    def process_modules(self) -> FrozenSet[str]:
        return self._process_modules

    @property
    def process_modules_explicit(self) -> bool:
        return self._process_modules_explicit

    def is_disabled(self, module_name: str) -> bool:
        return module_name in self._disabled

    def is_microservice_mode(self) -> bool:
        return self._module_runtime == 'microservice'

    def allows_module_process_os_services(self, module_name: str) -> bool:
        """OS-службы API/worker/beat модуля — только microservice и имя в MICROSERVICE_MODULES."""
        if not module_name or module_name in self._disabled:
            return False
        if not self.is_microservice_mode():
            return False
        return module_name in self._microservice_modules

    def is_split_mode(self) -> bool:
        """Устаревший алиас ``is_microservice_mode``."""
        return self.is_microservice_mode()

    def module_process_name(self) -> str | None:
        """Имя модуля для роли ``module:<name>``, иначе None."""
        role = self._process_role
        if role.startswith('module:'):
            name = role.split(':', 1)[1].strip()
            return name or None
        return None

    def is_core_api_process(self) -> bool:
        """HTTP-процесс ядра (start_api). ``start_api`` ставит ``ERGO_PROCESS_ROLE=api``."""
        return self._process_role in _CORE_API_PROCESS_ROLES

    def is_core_schedule_process(self) -> bool:
        """HTTP ядра или общий Beat."""
        return self._process_role in _CORE_SCHEDULE_PROCESS_ROLES

    def is_core_side_process(self) -> bool:
        """Не процесс модуля: API, beat, worker и команды Django без роли.

        Пустой ``ERGO_PROCESS_ROLE`` на ядре — тоже сторона ядра, а не «грузи все».
        """
        return self.module_process_name() is None

    def is_loadable_in_process(self, module_name: str) -> bool:
        """Модуль должен попасть в INSTALLED_APPS / URL discovery этого процесса."""
        if not module_name or module_name in self._disabled:
            return False

        if (
            self._colocate_enabled
            and module_name in self._colocated_modules
        ):
            return True

        if self._process_modules_explicit:
            return module_name in self._process_modules

        module_only = self.module_process_name()
        if module_only is not None:
            return module_name == module_only

        if self.is_microservice_mode() and module_name in self._microservice_modules:
            return False

        return True

    def process_filter_fingerprint(self) -> str:
        """Строка для инвалидации кэша discovered_apps при смене роли/режима."""
        allow = ','.join(sorted(self._process_modules)) if self._process_modules_explicit else ''
        ms = ','.join(sorted(self._microservice_modules))
        colocated = ','.join(sorted(self._colocated_modules))
        return (
            f'runtime={self._module_runtime};'
            f'role={self._process_role};'
            f'microservice={ms};'
            f'process={allow};'
            f'explicit={int(self._process_modules_explicit)};'
            f'colocate={int(self._colocate_enabled)};'
            f'colocated={colocated}'
        )

    def cache_key_suffix(self) -> str:
        """Суффикс файла кэша, чтобы разные роли не перезаписывали друг друга."""
        role = self._process_role or 'api'
        safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in role)
        return safe or 'api'

    @staticmethod
    def is_valid_module_dir_name(name: str) -> bool:
        if not name or name.startswith('.'):
            return False
        return name not in _SKIPPED_MODULE_DIR_NAMES

    @staticmethod
    def is_populated_module_dir(path: Path) -> bool:
        """
        Есть содержимое модуля, а не пустой placeholder (неинициализированный submodule).

        Как в client ``listEnabledModuleNames``: нужен ``api/`` и/или ``client/``.
        Пустые каталоги не считаются установленными модулями и не попадают в каталоги UI.
        """
        if not path.is_dir():
            return False
        return (path / 'api').is_dir() or (path / 'client').is_dir()

    def enabled_names(self) -> list[str]:
        return self.list_module_names(include_disabled=False)

    def list_module_names(self, *, include_disabled: bool = False) -> list[str]:
        if not self._modules_dir.is_dir():
            return []

        names: list[str] = []
        for entry in self._modules_dir.iterdir():
            if not entry.is_dir() or not self.is_valid_module_dir_name(entry.name):
                continue
            if not self.is_populated_module_dir(entry):
                continue
            if not include_disabled and entry.name in self._disabled:
                continue
            names.append(entry.name)
        return sorted(names)

    def list_loadable_module_names(self) -> list[str]:
        """Имена модулей, загружаемых в текущем процессе (disabled + process filter)."""
        return [n for n in self.list_module_names(include_disabled=False) if self.is_loadable_in_process(n)]

    def iter_module_dirs(self, *, include_disabled: bool = False) -> Iterator[Path]:
        for name in self.list_module_names(include_disabled=include_disabled):
            yield self._modules_dir / name
