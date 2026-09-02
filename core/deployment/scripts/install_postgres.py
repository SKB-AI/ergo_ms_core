"""
Установка portable PostgreSQL в virtual_env/packages/postgres
(Windows: EDB binaries zip; Linux: сборка из исходников latest stable).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
SCRIPTS_DIR = DEPLOYMENT_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from postgres_common import (  # noqa: E402
    DEFAULT_PORT,
    EDB_WINDOWS_URL,
    PG_FTP_SOURCE,
    _download,
    _ensure_layout,
    _exe,
    _initdb_if_needed,
    ensure_databases,
    has_system_postgresql_service,
    is_installed,
    load_db_defaults,
    ping_postgres,
    postgres_version_file,
    read_installed_version,
    read_portable_listen_port,
    resolve_latest_version,
    resolve_portable_bind,
    resolve_portable_listen_port,
    postgres_bin,
    postgres_packages_dir,
    start_server,
    stop_server,
)

# Django-аудит (core_audit) и поиск по журналу требуют contrib/pg_trgm.
LINUX_CONTRIB_EXTENSIONS = ('pg_trgm',)


def _force_install_from_env() -> bool:
    try:
        from deployment_env import is_postgres_force_install
    except ImportError:
        return False
    return is_postgres_force_install()


def _find_edb_pgsql_root(extract_dir: Path) -> Path:
    for candidate in (extract_dir / 'pgsql', extract_dir):
        if (candidate / 'bin' / _exe('postgres')).is_file():
            return candidate
    nested = list(extract_dir.rglob(_exe('postgres')))
    for postgres_path in nested:
        if postgres_path.parent.name == 'bin':
            return postgres_path.parent.parent
    raise RuntimeError(t('postgres_edb_not_found', extract_dir=extract_dir))


def _copy_tree_merge(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            _copy_tree_merge(item, target)
        else:
            shutil.copy2(item, target)


def _contrib_control_path(root: Path, name: str) -> Path:
    return postgres_packages_dir(root) / 'share' / 'extension' / f'{name}.control'


def _missing_linux_contrib(root: Path) -> tuple[str, ...]:
    return tuple(name for name in LINUX_CONTRIB_EXTENSIONS if not _contrib_control_path(root, name).is_file())


def _install_linux_contrib_from_source(root: Path, source: Path, *, jobs: str) -> None:
    """Собрать contrib рядом с уже установленным prefix (make install ядра уже был)."""
    missing = _missing_linux_contrib(root)
    if not missing:
        return
    pg_config = postgres_bin(root, 'pg_config')
    if not pg_config.is_file():
        raise RuntimeError(t('postgres_tool_not_found', tool='pg_config', binary=pg_config))
    for name in missing:
        contrib_dir = source / 'contrib' / name
        if not contrib_dir.is_dir():
            raise RuntimeError(t('postgres_contrib_source_missing', name=name, path=contrib_dir))
        print(format_console('info', t('postgres_contrib_building', name=name)))
        pg_cfg = f'PG_CONFIG={pg_config}'
        env = {**os.environ, 'PATH': f'{pg_config.parent}{os.pathsep}{os.environ.get("PATH", "")}'}
        subprocess.run(
            ['make', f'-j{jobs}', 'USE_PGXS=1', pg_cfg],
            cwd=str(contrib_dir),
            check=True,
            env=env,
        )
        subprocess.run(
            ['make', 'install', 'USE_PGXS=1', pg_cfg],
            cwd=str(contrib_dir),
            check=True,
            env=env,
        )
        if not _contrib_control_path(root, name).is_file():
            raise RuntimeError(t('postgres_contrib_install_failed', name=name))
        print(format_console('ok', t('postgres_contrib_ready', name=name)))


def ensure_linux_contrib_extensions(root: Path) -> None:
    """Дособрать pg_trgm и др. для уже стоящего portable (без полной пересборки ядра)."""
    if platform.system().lower() != 'linux':
        return
    if not is_installed(root):
        return
    missing = _missing_linux_contrib(root)
    if not missing:
        return
    version = read_installed_version(root)
    if not version:
        raise RuntimeError(t('postgres_contrib_no_version'))
    from download_cache import cached_archive_path, download_with_cache

    cache_tmp = root / 'virtual_env' / 'cache' / 'tmp'
    cache_tmp.mkdir(parents=True, exist_ok=True)
    tarball = f'postgresql-{version}.tar.bz2'
    url = f'{PG_FTP_SOURCE}v{version}/{tarball}'
    with tempfile.TemporaryDirectory(dir=str(cache_tmp)) as tmp:
        tmp_path = Path(tmp)
        tar_path = tmp_path / tarball
        if download_with_cache(
            root,
            'postgres',
            tar_path,
            lambda dest: _download(url, dest),
        ):
            print(
                format_console(
                    'info',
                    t(
                        'archive_using_cache',
                        path=cached_archive_path(root, 'postgres', tar_path.name),
                    ),
                )
            )
        extract_dir = tmp_path / 'src'
        extract_dir.mkdir()
        with tarfile.open(tar_path, 'r:bz2') as archive:
            archive.extractall(extract_dir)
        source_dirs = list(extract_dir.glob(f'postgresql-{version}'))
        if not source_dirs:
            raise RuntimeError(t('postgres_sources_not_found'))
        jobs = str(max(1, (os.cpu_count() or 2)))
        _install_linux_contrib_from_source(root, source_dirs[0], jobs=jobs)


def _install_windows(root: Path, version: str, force: bool) -> None:
    paths = _ensure_layout(root)
    installed = read_installed_version(root)
    if is_installed(root) and not force:
        if installed == version:
            print(format_console('skip', t('postgres_already_installed', version=version)))
            return
        print(format_console('info', t('postgres_upgrading', installed=installed, version=version)))
        force = True

    if force and paths['bin'].exists():
        stop_server(root)
        for name in ('bin', 'lib', 'share', 'include', 'doc', 'pgAdmin 4', 'StackBuilder'):
            target = paths['base'] / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
        pgsql = paths['base'] / 'pgsql'
        if pgsql.is_dir():
            shutil.rmtree(pgsql, ignore_errors=True)

    cache_tmp = root / 'virtual_env' / 'cache' / 'tmp'
    cache_tmp.mkdir(parents=True, exist_ok=True)
    url = EDB_WINDOWS_URL.format(version=version)
    with tempfile.TemporaryDirectory(dir=str(cache_tmp)) as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / f'postgresql-{version}-windows-x64-binaries.zip'
        from download_cache import cached_archive_path, download_with_cache

        if download_with_cache(
            root,
            'postgres',
            zip_path,
            lambda dest: _download(url, dest),
        ):
            print(
                format_console(
                    'info',
                    t(
                        'archive_using_cache',
                        path=cached_archive_path(root, 'postgres', zip_path.name),
                    ),
                )
            )
        extract_dir = tmp_path / 'extract'
        extract_dir.mkdir()
        print(t('postgres_unpacking'))
        with zipfile.ZipFile(zip_path, 'r') as archive:
            archive.extractall(extract_dir)
        source = _find_edb_pgsql_root(extract_dir)
        for name in ('bin', 'lib', 'share', 'include'):
            src = source / name
            if src.is_dir():
                _copy_tree_merge(src, paths['base'] / name)

    postgres_version_file(root).write_text(version + '\n', encoding='utf-8')
    print(format_console('ok', t('postgres_installed_at', version=version, base=paths['base'])))


def _install_linux(root: Path, version: str, force: bool) -> None:
    paths = _ensure_layout(root)
    if is_installed(root) and not force:
        installed = read_installed_version(root)
        if installed == version:
            print(format_console('skip', t('postgres_already_installed', version=version)))
            return
        print(format_console('info', t('postgres_upgrading', installed=installed, version=version)))
        force = True

    from linux_build_packages import ensure_linux_build_packages

    ensure_linux_build_packages('postgres')

    if force and paths['bin'].is_dir():
        stop_server(root)
        shutil.rmtree(paths['bin'], ignore_errors=True)

    cache_tmp = root / 'virtual_env' / 'cache' / 'tmp'
    cache_tmp.mkdir(parents=True, exist_ok=True)
    tarball = f'postgresql-{version}.tar.bz2'
    url = f'{PG_FTP_SOURCE}v{version}/{tarball}'
    with tempfile.TemporaryDirectory(dir=str(cache_tmp)) as tmp:
        tmp_path = Path(tmp)
        tar_path = tmp_path / tarball
        from download_cache import cached_archive_path, download_with_cache

        if download_with_cache(
            root,
            'postgres',
            tar_path,
            lambda dest: _download(url, dest),
        ):
            print(
                format_console(
                    'info',
                    t(
                        'archive_using_cache',
                        path=cached_archive_path(root, 'postgres', tar_path.name),
                    ),
                )
            )
        extract_dir = tmp_path / 'src'
        extract_dir.mkdir()
        with tarfile.open(tar_path, 'r:bz2') as archive:
            archive.extractall(extract_dir)
        source_dirs = list(extract_dir.glob(f'postgresql-{version}'))
        if not source_dirs:
            raise RuntimeError(t('postgres_sources_not_found'))
        source = source_dirs[0]
        prefix = paths['base']
        jobs = str(max(1, (os.cpu_count() or 2)))
        print(t('postgres_building', version=version))
        configure = ['./configure', f'--prefix={prefix}', '--without-icu']
        cfg = subprocess.run(
            [*configure, '--with-openssl', '--with-lz4'],
            cwd=str(source),
            check=False,
        )
        if cfg.returncode != 0:
            print(format_console('warning', t('postgres_lz4_configure_fallback')))
            cfg = subprocess.run([*configure, '--with-openssl'], cwd=str(source), check=False)
        if cfg.returncode != 0:
            print(format_console('warning', t('postgres_openssl_configure_fallback')))
            subprocess.run(configure, cwd=str(source), check=True)
        subprocess.run(['make', f'-j{jobs}'], cwd=str(source), check=True)
        subprocess.run(['make', 'install'], cwd=str(source), check=True)
        _install_linux_contrib_from_source(root, source, jobs=jobs)

    postgres_version_file(root).write_text(version + '\n', encoding='utf-8')
    print(format_console('ok', t('postgres_installed_at', version=version, base=paths['base'])))


def install_postgres(
    root: Path,
    *,
    port: int | None = None,
    force: bool = False,
    platform_name: str = 'auto',
    skip_if_system: bool = True,
    start: bool = True,
    create_db: bool = True,
) -> int:
    """
    Returns:
        0 — OK / SKIP
        1 — ошибка
        2 — SKIP из‑за системной службы
    """
    if _force_install_from_env():
        skip_if_system = False

    system_present = has_system_postgresql_service()
    if skip_if_system and system_present:
        print(format_console('skip', t('postgres_system_skip_portable')))
        print(format_console('info', t('postgres_force_hint')))
        return 2

    alongside_system = (not skip_if_system) and system_present
    listen_port = resolve_portable_listen_port(
        root,
        cli_port=port,
        alongside_system=alongside_system,
    )
    listen_bind = resolve_portable_bind(root)

    if alongside_system:
        print(format_console(
            'warning',
            t('postgres_force_with_system_port', listen_port=listen_port),
        ))

    print(format_console(
        'info',
        t(
            'postgres_listen_info',
            listen_bind=listen_bind,
            listen_port=listen_port,
            default_port=DEFAULT_PORT,
        ),
    ))
    yaml_port = None
    try:
        yaml_port = int(load_db_defaults(root).get('port') or 0) or None
    except ValueError:
        yaml_port = None
    # Порт уже из yaml — подсказка только если CLI переопределил
    if port is not None and yaml_port is not None and yaml_port != listen_port:
        print(format_console(
            'info',
            t('postgres_cli_port_differs', listen_port=listen_port, yaml_port=yaml_port),
        ))

    system = platform_name.lower()
    if system == 'auto':
        system = platform.system().lower()

    # Повторный setup: не ходить на FTP за latest, если portable уже стоит.
    installed_version = read_installed_version(root) if is_installed(root) else None
    if installed_version and not force:
        version = installed_version
        print(format_console('skip', t('postgres_already_installed', version=version)))
    else:
        version = resolve_latest_version()
        print(format_console('info', t('postgres_target_version', version=version)))
    print(format_console('info', t('postgres_listen_port_info', listen_port=listen_port)))

    from security.ensure_infra_credentials import ensure_infra_credentials

    ensure_infra_credentials(root)
    defaults = load_db_defaults(root)
    user = defaults['user']
    password = defaults['password']

    try:
        if not (installed_version and not force):
            if system == 'windows':
                _install_windows(root, version, force)
            elif system == 'linux':
                _install_linux(root, version, force)
            else:
                raise RuntimeError(t('postgres_unsupported_platform', system=system))

        if system == 'linux':
            ensure_linux_contrib_extensions(root)
        _initdb_if_needed(root, user, password, listen_port, listen_bind)
        if start:
            start_server(root)
        if create_db and start:
            ensure_databases(root, port=listen_port)
    except Exception as exc:
        print(format_console('error', str(exc)), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description='Install portable PostgreSQL into virtual_env/packages/postgres'
    )
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--platform', default='auto', choices=('auto', 'windows', 'linux'))
    parser.add_argument('--ping-only', action='store_true')
    parser.add_argument('--check-system-only', action='store_true')
    parser.add_argument(
        '--check-force-only',
        action='store_true',
        help='Exit 0 if POSTGRES_FORCE_INSTALL is set, else 1',
    )
    parser.add_argument('--ensure-db-only', action='store_true')
    parser.add_argument('--print-port', action='store_true')
    parser.add_argument('--print-bind', action='store_true')
    parser.add_argument('--no-skip-system', action='store_true')
    parser.add_argument('--no-start', action='store_true')
    args = parser.parse_args()

    if args.check_system_only:
        return 0 if has_system_postgresql_service() else 1

    if args.check_force_only:
        return 0 if _force_install_from_env() else 1

    if args.print_port:
        stored = read_portable_listen_port(args.root)
        print(stored if stored is not None else resolve_portable_listen_port(args.root))
        return 0

    if args.print_bind:
        print(resolve_portable_bind(args.root))
        return 0

    if args.ping_only:
        return 0 if ping_postgres(args.root, port=args.port) else 1

    if args.ensure_db_only:
        try:
            ensure_databases(args.root, port=args.port)
        except Exception as exc:
            print(format_console('error', str(exc)), file=sys.stderr)
            return 1
        return 0

    code = install_postgres(
        args.root,
        port=args.port,
        force=args.force,
        platform_name=args.platform,
        skip_if_system=not args.no_skip_system,
        start=not args.no_start,
        create_db=not args.no_start,
    )
    return 0 if code in (0, 2) else 1


if __name__ == '__main__':
    raise SystemExit(main())
