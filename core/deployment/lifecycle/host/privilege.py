"""Повышение привилегий на Linux (sudo re-exec) и возврат владельца проекта."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Артефакты, которые sudo / служба без User= часто оставляют у root.
RUNTIME_ARTIFACT_RELATIVE = (
    'virtual_env/packages/redis',
    'virtual_env/packages/nginx',
    'virtual_env/packages/postgres',
    'virtual_env/client-remotes',
    'virtual_env/npm',
    'virtual_env/cache',
    'virtual_env/celery',
    'core/client/node_modules',
    'core/client/dist',
    'logs',
    'core/deployment/wrappers',
)

CLIENT_BUILD_ARTIFACT_RELATIVE = (
    'core/client/dist',
    'core/client/node_modules',
    'virtual_env/client-remotes',
    'virtual_env/npm',
    'virtual_env/cache',
    'logs',
)


def is_root() -> bool:
    if os.name == 'nt':
        return False
    return os.geteuid() == 0


def needs_sudo_reexec(recipe_needs_sudo: bool) -> bool:
    if not recipe_needs_sudo:
        return False
    if sys.platform == 'win32':
        return False
    if is_root():
        return False
    return shutil.which('sudo') is not None


def sudo_env_assignments() -> list[str]:
    """Переменные, которые sudo должен протащить без ``-E``."""
    assignments: list[str] = []
    attached = os.environ.get('ERGO_CLI_LOG_ATTACHED', '').strip()
    if attached:
        assignments.append(f'ERGO_CLI_LOG_ATTACHED={attached}')
    return assignments


def reexec_with_sudo(argv: list[str], *, cwd: Path | None = None) -> int:
    cmd = ['sudo', *sudo_env_assignments(), *argv]
    return subprocess.call(cmd, cwd=str(cwd) if cwd else None)


def project_owner_ids(root: Path) -> tuple[int, int]:
    st = Path(root).stat()
    return st.st_uid, st.st_gid


def drop_to_project_owner(root: Path) -> bool:
    """Если процесс root — перейти на uid/gid владельца корня, без имени в коде."""
    if os.name == 'nt' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return False
    uid, gid = project_owner_ids(root)
    if uid == 0:
        return False
    try:
        import pwd

        pw = pwd.getpwuid(uid)
        os.environ['HOME'] = pw.pw_dir
        os.environ['USER'] = pw.pw_name
        os.environ['LOGNAME'] = pw.pw_name
        os.initgroups(pw.pw_name, gid)
    except KeyError:
        pass
    os.setgid(gid)
    os.setuid(uid)
    return True


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    if path.is_symlink() or path.is_file():
        os.chown(path, uid, gid, follow_symlinks=False)
        return
    for dirpath, dirnames, filenames in os.walk(path):
        os.chown(dirpath, uid, gid)
        for name in (*dirnames, *filenames):
            os.chown(Path(dirpath) / name, uid, gid, follow_symlinks=False)


def _tree_has_foreign_owner(path: Path, uid: int) -> bool:
    try:
        if path.stat().st_uid != uid:
            return True
    except OSError:
        return True
    if not path.is_dir():
        return False
    for dirpath, dirnames, filenames in os.walk(path):
        for name in (*dirnames, *filenames):
            try:
                if (Path(dirpath) / name).stat().st_uid != uid:
                    return True
            except OSError:
                return True
    return False


def restore_project_ownership(root: Path, path: Path) -> bool:
    """После sudo-установки вернуть файлы владельцу корня проекта."""
    if os.name == 'nt':
        return True
    target = Path(path)
    if not target.exists():
        return True
    uid, gid = project_owner_ids(root)
    if uid == 0:
        return True
    if is_root():
        _chown_tree(target, uid, gid)
        return True
    if not _tree_has_foreign_owner(target, uid):
        return True
    sudo = shutil.which('sudo')
    if sudo is None:
        return False
    result = subprocess.run(
        [sudo, *sudo_env_assignments(), 'chown', '-R', f'{uid}:{gid}', str(target)],
        check=False,
    )
    return result.returncode == 0


def restore_paths_ownership(root: Path, relatives: tuple[str, ...]) -> None:
    project = Path(root)
    for relative in relatives:
        restore_project_ownership(project, project / relative)


def restore_runtime_artifact_ownership(root: Path) -> None:
    """Вернуть типичные runtime-артефакты владельцу проекта (кэш Vite, пакеты, логи)."""
    restore_paths_ownership(root, RUNTIME_ARTIFACT_RELATIVE)


def restore_client_build_ownership(root: Path) -> None:
    """Сборка клиента и логи после неё принадлежат владельцу корня, не root."""
    restore_paths_ownership(root, CLIENT_BUILD_ARTIFACT_RELATIVE)
