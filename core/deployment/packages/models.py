"""Модели portable-пакетов ERGO MS."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class PackageKind(str, Enum):
    ARCHIVE = 'archive'
    CUSTOM = 'custom'


class PackageSource(str, Enum):
    CORE = 'core'
    MODULE = 'module'


@dataclass(frozen=True)
class PlatformArchive:
    """Параметры archive-установки для одной платформы."""

    url: str
    archive: str  # zip | tar.gz | tgz | tar.xz
    marker: str
    pick_match: str | None = None
    pick_as: str | None = None
    strip_top_dir: bool = False


@dataclass(frozen=True)
class PackageSpec:
    """Описание пакета из core_packages.yaml или modules/*/packages.yaml."""

    name: str
    kind: PackageKind
    dest: str
    source: PackageSource
    version: str = ''
    module: str | None = None
    module_dir: Path | None = None
    installer: str | None = None
    installer_args: tuple[str, ...] = ()
    pass_root: bool = True
    marker_windows: str | None = None
    marker_linux: str | None = None
    extra_dirs: tuple[str, ...] = ()
    always_run: bool = False
    platforms: dict[str, PlatformArchive] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class PackageStatus:
    """Фактическое состояние пакета на диске."""

    name: str
    installed: bool
    path: Path
    version: str
    source: PackageSource
    module: str | None
    kind: PackageKind
    marker: str | None
    platform_supported: bool
