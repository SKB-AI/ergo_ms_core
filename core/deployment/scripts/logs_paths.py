"""Единый каталог и имена файлов логов ERGO MS (без Django)."""

from __future__ import annotations

import re
from pathlib import Path

from deployment_env import PROJECT_ROOT
from log_env import (
    log_basename,
    resolve_logs_dir,
    service_log_map,
)

_MODULE_PROCESS_UNIT_RE = re.compile(r'_module_(.+)_(api|worker|beat)$')


def ensure_logs_dir(project_root: Path | None = None) -> Path:
    logs_dir = resolve_logs_dir(project_root)
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def log_file_path(name: str, project_root: Path | None = None) -> Path:
    return resolve_logs_dir(project_root) / name


def service_stderr_log(service_name: str, project_root: Path | None = None) -> Path:
    safe = service_name.replace('.service', '')
    return log_file_path(f'{safe}.stderr.log', project_root)


def _module_process_log_key(kind: str) -> str | None:
    if kind == 'api':
        return 'API'
    if kind == 'worker':
        return 'CELERY_WORKER'
    if kind == 'beat':
        return 'CELERY_BEAT'
    return None


def parse_module_process_unit(service_name: str) -> tuple[str, str] | None:
    """Любой префикс: ``*_module_<name>_api|worker|beat``."""
    base = service_name.replace('.service', '')
    match = _MODULE_PROCESS_UNIT_RE.search(base)
    if match is None:
        return None
    return match.group(1), match.group(2)


def is_known_service_log(service_name: str, project_root: Path | None = None) -> bool:
    """Имя, для которого есть явный файл лога, даже если OS-службы нет на этом хосте."""
    if not service_name:
        return False
    base = service_name.replace('.service', '')
    root = project_root or PROJECT_ROOT
    from service_names import names_from_root

    svc_names = names_from_root(root)
    if svc_names.is_celery_worker(base) or base.startswith('ergo-celery-worker-'):
        return True
    if parse_module_process_unit(base) is not None:
        return True
    if base in service_log_map(project_root):
        return True
    if base in (
        svc_names.postgres,
        'ergo-postgres',
        f'{svc_names.prefix}_db',
        f'{svc_names.prefix}_sqlite',
        f'{svc_names.prefix}_mysql',
        f'{svc_names.prefix}_mssql',
    ):
        return True
    return False


def resolve_service_log_files(service_name: str, project_root: Path | None = None) -> list[Path]:
    base = service_name.replace('.service', '')
    logs_dir = resolve_logs_dir(project_root)
    mapping = service_log_map(project_root)
    root = project_root or PROJECT_ROOT

    from service_names import names_from_root

    svc_names = names_from_root(root)
    if svc_names.is_celery_worker(base) or base.startswith('ergo-celery-worker-'):
        return [logs_dir / log_basename('CELERY_WORKER', project_root)]

    parsed_module = parse_module_process_unit(base)
    if parsed_module is not None:
        log_key = _module_process_log_key(parsed_module[1])
        if log_key:
            return [logs_dir / log_basename(log_key, project_root)]

    if base in (
        svc_names.postgres,
        'ergo-postgres',
        f'{svc_names.prefix}_db',
        f'{svc_names.prefix}_sqlite',
        f'{svc_names.prefix}_mysql',
        f'{svc_names.prefix}_mssql',
    ):
        from start_db_logs_dev import resolve_default_db_log_paths  # noqa: WPS433

        paths = resolve_default_db_log_paths(root=root)
        existing = [path for path in paths if path.is_file()]
        return existing or (paths[:1] if paths else [logs_dir / f'{base}.log'])

    names = mapping.get(base)
    if names:
        return [logs_dir / name for name in names]

    legacy = logs_dir / f'{base}.log'
    if legacy.is_file():
        return [legacy]
    return [legacy]


def service_log_files_posix(service_name: str, project_root: Path | None = None) -> list[str]:
    return [str(path).replace('\\', '/') for path in resolve_service_log_files(service_name, project_root)]


def celery_tasks_module_pattern(module_name: str) -> str:
    """Фильтр хвоста celery_tasks.log: задачи и код модуля в процессе worker."""
    return rf'(celery\.module\.{module_name}|modules\.{module_name})'


def _cli_main() -> int:
    import sys

    if len(sys.argv) < 2:
        print(resolve_logs_dir(), end='')
        return 0

    command = sys.argv[1]
    if command == 'dir':
        root = Path(sys.argv[2]) if len(sys.argv) >= 3 else PROJECT_ROOT
        print(resolve_logs_dir(root), end='')
        return 0

    if command == 'service' and len(sys.argv) >= 3:
        root = Path(sys.argv[3]) if len(sys.argv) >= 4 else PROJECT_ROOT
        for path in resolve_service_log_files(sys.argv[2], root):
            print(path)
        return 0

    if command == 'stderr' and len(sys.argv) >= 3:
        root = Path(sys.argv[3]) if len(sys.argv) >= 4 else PROJECT_ROOT
        print(service_stderr_log(sys.argv[2], root), end='')
        return 0

    if command == 'known' and len(sys.argv) >= 3:
        root = Path(sys.argv[3]) if len(sys.argv) >= 4 else PROJECT_ROOT
        print('true' if is_known_service_log(sys.argv[2], root) else 'false', end='')
        return 0

    print(f'Неизвестная команда: {command}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(_cli_main())
