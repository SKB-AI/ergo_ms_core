"""Изолированный worktree: setup-full без служб ОС."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import subprocess
from pathlib import Path

from project_layout import cache_dir, ensure_dir
from scenario_test.isolation import workspace_config_fingerprint
from scenario_test.ports import pick_scenario_ports
from service_names import PREFIX_ENV

from .environment import IsolatedEnvironment, venv_python
from .processes import start_api, start_client, stop_api, stop_proc


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, OSError):
        return False


def _is_placeholder_dir(path: Path) -> bool:
    if not path.is_dir() or _is_reparse_point(path):
        return False
    names = {item.name for item in path.iterdir()}
    return names <= {'.gitkeep'}


def _is_inside_tree(path: Path, tree_root: Path) -> bool:
    try:
        path.absolute().relative_to(tree_root.absolute())
        return True
    except ValueError:
        return False


def _remove_under_tree(path: Path, tree_root: Path) -> None:
    """Удаляет каталог только внутри worktree. Junction снимает, не трогая цель."""
    if not path.exists() and not _is_reparse_point(path):
        return
    if not _is_inside_tree(path, tree_root):
        raise RuntimeError(f'отказ удалять путь вне worktree: {path}')
    if _is_reparse_point(path):
        path.rmdir()
        return
    shutil.rmtree(path)


def link_host_dir(source: Path, target: Path) -> None:
    """Junction/symlink на каталог хоста. Пустой .gitkeep из git не блокирует связь."""
    if not source.is_dir():
        return
    if target.exists() and not _is_placeholder_dir(target):
        return
    if _is_placeholder_dir(target):
        shutil.rmtree(target)
    ensure_dir(target.parent)
    if os.name == 'nt':
        subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(target), str(source)],
            check=False,
            capture_output=True,
        )
        return
    target.symlink_to(source, target_is_directory=True)


class HostWorktreeEnvironment(IsolatedEnvironment):
    kind = 'host'

    def __init__(self, workspace: Path, run_dir: Path, prefix: str) -> None:
        super().__init__(workspace, run_dir, prefix)
        self._fingerprint = workspace_config_fingerprint(workspace)
        self._used_worktree = False
        self._ports: dict[str, int] = {}
        self._api = None
        self._client = None

    def provision(self) -> None:
        ensure_dir(self.run_dir)
        self._create_tree()
        self._link_download_cache()
        self._write_throwaway_config()
        result = self.run_ergoms('setup-full', timeout=7200)
        if result.returncode != 0:
            detail = ((result.stderr or '') + '\n' + (result.stdout or ''))[-2000:]
            raise RuntimeError(detail or f'setup-full exit {result.returncode}')

    def start(self) -> None:
        python = venv_python(self.tree_root)
        if not python.is_file():
            raise RuntimeError(f'нет venv python: {python}')

    def ensure_api(self) -> None:
        if self._api is not None:
            return
        yaml_path = self.tree_root / 'databases.yaml'
        port = int(self._ports.get('api', 18000))
        client_origin = self.client_base()
        self._api = start_api(
            self.tree_root,
            yaml_path=yaml_path,
            port=port,
            extra_env={
                PREFIX_ENV: self.prefix,
                'DEBUG': 'false',
                'FRONTEND_BASE_URL': client_origin,
                'CORS_ALLOWED_ORIGINS': f'{client_origin},http://localhost:{self._ports.get("client", 18001)}',
                'CSRF_TRUSTED_ORIGINS': f'{client_origin},http://localhost:{self._ports.get("client", 18001)}',
                'API_ALLOWED_HOSTS': 'localhost,127.0.0.1',
            },
        )

    def start_client(self) -> None:
        self.ensure_api()
        if self._client is not None:
            return
        extra = {PREFIX_ENV: self.prefix}
        extra['API_PORT'] = str(self._ports.get('api', 18000))
        extra['CLIENT_PORT'] = str(self._ports.get('client', 18001))
        self._client = start_client(self.tree_root, client_url=self.client_base(), extra_env=extra)

    def http_base(self) -> str:
        port = self._ports.get('api', 8000)
        return f'http://127.0.0.1:{port}'

    def client_base(self) -> str:
        port = self._ports.get('client', 8001)
        return f'http://127.0.0.1:{port}'

    def teardown(self) -> None:
        stop_proc(self._client)
        self._client = None
        stop_api(self._api)
        self._api = None
        try:
            self.run_ergoms('clean', timeout=600)
        except Exception:
            pass
        if self._used_worktree and self.tree_root.is_dir():
            subprocess.run(
                ['git', 'worktree', 'remove', '--force', str(self.tree_root)],
                cwd=str(self.workspace),
                capture_output=True,
                check=False,
            )
        elif self.tree_root.is_dir():
            shutil.rmtree(self.tree_root, ignore_errors=True)
        after = workspace_config_fingerprint(self.workspace)
        if after != self._fingerprint:
            raise RuntimeError('рабочие .env или databases.yaml изменились во время прогона')

    def _create_tree(self) -> None:
        result = subprocess.run(
            ['git', 'worktree', 'add', '--detach', str(self.tree_root), 'HEAD'],
            cwd=str(self.workspace),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            self._used_worktree = True
            self._overlay_workspace_deployment()
            self._link_workspace_checkouts()
            return
        ignore = shutil.ignore_patterns(
            'virtual_env',
            'logs',
            '.git',
            'node_modules',
            '__pycache__',
            '.env',
            'dist',
        )
        shutil.copytree(self.workspace, self.tree_root, ignore=ignore, dirs_exist_ok=True)
        self._overlay_workspace_deployment()
        self._link_workspace_checkouts()

    def _overlay_workspace_deployment(self) -> None:
        """Git worktree — HEAD; поверх кладём текущие файлы развёртывания."""
        source = self.workspace / 'core' / 'deployment'
        target = self.tree_root / 'core' / 'deployment'
        if not source.is_dir() or not target.parent.is_dir():
            return
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                '__pycache__',
                '*.pyc',
                'wrappers',
                '*.generated.yml',
                '.compose.env',
                '.compose.databases.yaml',
            ),
        )

    def _link_workspace_checkouts(self) -> None:
        """git worktree не заполняет submodule. Junction Docker на Windows не видит."""
        pairs = (
            (self.workspace / 'core' / 'api', self.tree_root / 'core' / 'api'),
            (self.workspace / 'core' / 'client', self.tree_root / 'core' / 'client'),
            (self.workspace / 'core' / 'media_api', self.tree_root / 'core' / 'media_api'),
        )
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.git', 'node_modules')
        for source, target in pairs:
            if not source.is_dir():
                continue
            ready = (
                not _is_reparse_point(target)
                and (
                    (target / 'commands' / '__main__.py').is_file()
                    or (target / 'package.json').is_file()
                    or (target / 'src').is_dir()
                )
            )
            if ready:
                continue
            if target.exists() or _is_reparse_point(target):
                _remove_under_tree(target, self.tree_root)
            shutil.copytree(source, target, ignore=ignore, dirs_exist_ok=True)

    def _link_download_cache(self) -> None:
        source = cache_dir(self.workspace) / 'downloads'
        if not source.is_dir():
            return
        target_parent = ensure_dir(cache_dir(self.tree_root))
        target = target_parent / 'downloads'
        if target.exists() and not _is_placeholder_dir(target):
            return
        try:
            link_host_dir(source, target)
        except OSError:
            pass

    def _link_host_python(self) -> None:
        pairs = (
            (self.workspace / 'virtual_env' / 'python', self.tree_root / 'virtual_env' / 'python'),
            (
                self.workspace / 'virtual_env' / 'packages' / 'python',
                self.tree_root / 'virtual_env' / 'packages' / 'python',
            ),
        )
        for source, target in pairs:
            try:
                link_host_dir(source, target)
            except OSError:
                pass

    def _write_throwaway_config(self) -> None:
        ports = pick_scenario_ports()
        if not ports:
            raise RuntimeError('нет свободных портов для изолированного worktree')
        self._ports = ports
        env_text = (
            f'{PREFIX_ENV}={self.prefix}\n'
            'ERGO_RUNTIME=host\n'
            'ERGO_PROXY=none\n'
            'ERGO_BROKER=local\n'
            'ERGO_DB=sqlite\n'
            'ERGO_SEARCH_ENABLED=false\n'
            'ERGO_JUPYTER=none\n'
            'MODULE_RUNTIME=monolith\n'
            'HOST_PROFILE=full\n'
            f'API_PORT={self._ports.get("api", 18000)}\n'
            f'CLIENT_PORT={self._ports.get("client", 18001)}\n'
            f'FRONTEND_BASE_URL=http://127.0.0.1:{self._ports.get("client", 18001)}\n'
            f'CORS_ALLOWED_ORIGINS=http://127.0.0.1:{self._ports.get("client", 18001)},http://localhost:{self._ports.get("client", 18001)}\n'
            f'CSRF_TRUSTED_ORIGINS=http://127.0.0.1:{self._ports.get("client", 18001)},http://localhost:{self._ports.get("client", 18001)}\n'
            'API_ALLOWED_HOSTS=localhost,127.0.0.1\n'
            f'API_SECRET_KEY={secrets.token_hex(32)}\n'
            f'API_JWT_SIGNING_KEY={secrets.token_hex(32)}\n'
            'DEBUG=false\n'
            'ERGO_LOG_CONSOLE=false\n'
            'API_AUTORELOAD=false\n'
        )
        (self.tree_root / '.env').write_text(env_text, encoding='utf-8')
        db_path = (self.run_dir / 'scenario.sqlite3').as_posix()
        (self.tree_root / 'databases.yaml').write_text(
            'default:\n'
            '  engine: sqlite\n'
            f'  name: {db_path}\n',
            encoding='utf-8',
        )
