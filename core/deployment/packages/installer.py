"""Установка и удаление portable-пакетов по PackageSpec."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

_PACKAGES_PKG_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _PACKAGES_PKG_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from console_tags import format_console  # noqa: E402
from project_layout import package_dir, packages_dir  # noqa: E402

from .archive import ArchiveInstallError, install_archive_package  # noqa: E402
from .models import PackageKind, PackageSpec, PackageStatus, PlatformArchive  # noqa: E402
from .registry import PackageRegistryError, discover_packages, get_package  # noqa: E402


class PackageInstallError(Exception):
    """Ошибка установки или удаления пакета."""


def current_platform_key() -> str:
    system = platform.system().lower()
    if system.startswith('win'):
        return 'windows'
    if system.startswith('linux'):
        return 'linux'
    if system == 'darwin':
        return 'darwin'
    return system


def resolve_platform_archive(spec: PackageSpec) -> PlatformArchive | None:
    key = current_platform_key()
    if key in spec.platforms:
        return spec.platforms[key]
    return None


def marker_relative(spec: PackageSpec) -> str | None:
    plat = resolve_platform_archive(spec)
    if plat is not None:
        return plat.marker
    key = current_platform_key()
    if key == 'windows':
        return spec.marker_windows
    return spec.marker_linux


def is_platform_supported(spec: PackageSpec) -> bool:
    if spec.kind == PackageKind.ARCHIVE:
        return resolve_platform_archive(spec) is not None
    # custom: считаем поддерживаемым, если есть marker для ОС или marker не задан
    key = current_platform_key()
    if key == 'windows':
        return True
    if key == 'linux':
        return True
    # darwin и прочее — custom может сам решить
    return True


def is_installed(root: Path, spec: PackageSpec) -> bool:
    rel = marker_relative(spec)
    if not rel:
        dest = package_dir(root, spec.dest)
        return dest.is_dir() and any(dest.iterdir())
    marker = package_dir(root, spec.dest) / rel
    if not marker.is_file():
        return False
    try:
        return marker.stat().st_size > 0
    except OSError:
        return False


def status_for(root: Path, spec: PackageSpec) -> PackageStatus:
    supported = is_platform_supported(spec)
    return PackageStatus(
        name=spec.name,
        installed=is_installed(root, spec) if supported else False,
        path=package_dir(root, spec.dest),
        version=spec.version,
        source=spec.source,
        module=spec.module,
        kind=spec.kind,
        marker=marker_relative(spec),
        platform_supported=supported,
    )


def _resolve_installer_script(root: Path, spec: PackageSpec) -> Path:
    assert spec.installer
    installer = spec.installer.replace('\\', '/')
    if Path(installer).is_absolute():
        path = Path(installer)
    elif spec.module_dir is not None:
        path = (spec.module_dir / installer).resolve()
    else:
        path = (_DEPLOYMENT_DIR / installer).resolve()
    if not path.is_file():
        raise PackageInstallError(f'Скрипт установщика не найден: {path}')
    # Не даём уйти за пределы корня проекта (и module_dir / deployment)
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PackageInstallError(f'Установщик вне корня проекта: {path}') from exc
    return path


def _run_custom_installer(root: Path, spec: PackageSpec, *, force: bool) -> int:
    script = _resolve_installer_script(root, spec)
    python = sys.executable
    argv = [python, str(script), *spec.installer_args]
    if script.suffix.lower() == '.py':
        args_lower = {a.lower() for a in spec.installer_args}
        if spec.pass_root and '--root' not in args_lower:
            argv.extend(['--root', str(root)])
        if force and '--force' not in args_lower:
            argv.append('--force')
    print(format_console('info', f'Запуск установщика: {script.name}'))
    result = subprocess.run(argv, cwd=str(root), check=False)
    return int(result.returncode)


def install_package(
    root: Path,
    name: str,
    *,
    force: bool = False,
    refresh: bool = False,
) -> int:
    """Установить пакет по имени. Код выхода 0/1."""
    try:
        spec = get_package(root, name)
    except PackageRegistryError as exc:
        print(format_console('error', str(exc)), file=sys.stderr)
        return 1
    if spec is None:
        print(format_console('error', f'Пакет не найден в реестре: {name}'), file=sys.stderr)
        return 1

    if not is_platform_supported(spec):
        print(
            format_console(
                'skip',
                f'Пакет {name} не поддерживается на платформе {current_platform_key()}',
            )
        )
        return 0

    if is_installed(root, spec) and not force and not spec.always_run:
        print(format_console('skip', f'{name} уже установлен: {package_dir(root, spec.dest)}'))
        return 0

    try:
        if spec.kind == PackageKind.ARCHIVE:
            plat = resolve_platform_archive(spec)
            if plat is None:
                print(
                    format_console(
                        'skip',
                        f'Пакет {name} не поддерживается на платформе {current_platform_key()}',
                    )
                )
                return 0
            install_archive_package(root, spec, plat, force=force, refresh=refresh)
            return 0
        code = _run_custom_installer(root, spec, force=force)
        if code != 0:
            print(format_console('error', f'Установщик пакета {name} завершился с кодом {code}'), file=sys.stderr)
        return code
    except (ArchiveInstallError, PackageInstallError) as exc:
        print(format_console('error', str(exc)), file=sys.stderr)
        return 1


def uninstall_package(
    root: Path,
    name: str,
    *,
    purge_extra: bool = False,
) -> int:
    """Удалить dest пакета; при purge_extra — также extra_dirs."""
    try:
        spec = get_package(root, name)
    except PackageRegistryError as exc:
        print(format_console('error', str(exc)), file=sys.stderr)
        return 1
    if spec is None:
        print(format_console('error', f'Пакет не найден в реестре: {name}'), file=sys.stderr)
        return 1

    dest = package_dir(root, spec.dest)
    try:
        if dest.exists():
            print(format_console('info', f'Удаление {dest}'))
            shutil.rmtree(dest)
            print(format_console('ok', f'Пакет {name} удалён'))
        else:
            print(format_console('skip', f'Каталог пакета не найден: {dest}'))

        if purge_extra:
            base = packages_dir(root)
            for extra in spec.extra_dirs:
                extra_path = base / extra
                if extra_path.exists():
                    print(format_console('info', f'Удаление extra: {extra_path}'))
                    shutil.rmtree(extra_path, ignore_errors=True)
                    print(format_console('ok', f'Удалено: {extra}'))
    except OSError as exc:
        print(format_console('error', f'Не удалось удалить пакет {name}: {exc}'), file=sys.stderr)
        return 1
    return 0


def list_statuses(root: Path) -> list[PackageStatus]:
    specs = discover_packages(root)
    return [status_for(root, specs[name]) for name in sorted(specs)]
