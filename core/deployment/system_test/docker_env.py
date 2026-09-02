"""Изолированный Docker-стек: docker-init / docker-up, не сырой compose."""

from __future__ import annotations

import secrets
import subprocess

from project_layout import ensure_dir
from scenario_test.ports import pick_scenario_ports
from scenario_test.sidecars import docker_available
from service_names import PREFIX_ENV

from .environment import venv_python
from .http import wait_http
from .worktree_env import HostWorktreeEnvironment


class SkipEnvironment(RuntimeError):
    """Окружение нельзя поднять — кейс должен стать SKIP, не FAIL."""


class DockerEnvironment(HostWorktreeEnvironment):
    kind = 'docker'

    def __init__(
        self,
        workspace,
        run_dir,
        prefix: str,
        spec_id: str = 'docker_direct',
    ) -> None:
        super().__init__(workspace, run_dir, prefix)
        self.spec_id = spec_id

    def provision(self) -> None:
        if not docker_available():
            raise SkipEnvironment('Docker недоступен — изолированный стек не поднимаем')
        ensure_dir(self.run_dir)
        self._create_tree()
        self._link_download_cache()
        self._link_host_python()
        python = venv_python(self.tree_root)
        if not python.is_file():
            raise RuntimeError(f'нет venv python в worktree: {python}')
        self._write_throwaway_config()
        self._seed_poetry_volume()
        result = self.run_ergoms('docker-init', timeout=7200)
        self._store_cmd_log('docker-init', result)
        if result.returncode != 0:
            detail = ((result.stderr or '') + '\n' + (result.stdout or ''))[-2000:]
            lowered = detail.lower()
            if 'docker' in lowered and ('not found' in lowered or 'не найден' in lowered):
                raise SkipEnvironment('Docker CLI недоступен')
            raise RuntimeError(detail or f'docker-init exit {result.returncode}')

    def start(self) -> None:
        result = self.run_ergoms('docker-up', timeout=1800)
        self._store_cmd_log('docker-up', result)
        if result.returncode != 0:
            detail = ((result.stderr or '') + '\n' + (result.stdout or ''))[-1500:]
            raise RuntimeError(detail or f'docker-up exit {result.returncode}')
        self._sync_ports_from_compose_env()
        try:
            wait_http(self.http_base(), timeout_sec=240.0, path='/api/system/ready/')
        except RuntimeError as exc:
            raise RuntimeError(f'{exc}; {self._http_debug_detail()}') from None

    def ensure_api(self) -> None:
        wait_http(self.http_base(), timeout_sec=180.0, path='/api/system/ready/')

    def _sync_ports_from_compose_env(self) -> None:
        path = self.tree_root / 'core' / 'deployment' / 'docker' / '.compose.env'
        if not path.is_file():
            return
        values: dict[str, str] = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            key, value = stripped.split('=', 1)
            values[key.strip()] = value.strip()
        for name in ('api', 'client'):
            raw = values.get(f'{name.upper()}_PORT', '')
            if raw.isdigit():
                self._ports[name] = int(raw)

    def _http_debug_detail(self) -> str:
        compose = self.tree_root / 'core' / 'deployment' / 'docker' / '.compose.env'
        ports = f'ports={self._ports!r} compose_env={compose.is_file()}'
        probe = self.run_ergoms('docker-ps', timeout=60)
        tail = ((probe.stderr or '') + '\n' + (probe.stdout or ''))[-800:]
        return f'{ports} docker-ps={probe.returncode} {tail}'.strip()

    def start_client(self) -> None:
        self.ensure_api()
        wait_http(self.client_base(), timeout_sec=120.0, path='/')

    def teardown(self) -> None:
        try:
            self.run_ergoms('docker-down', '-v', '--remove-orphans', timeout=1800)
        except Exception:
            pass
        self._force_remove_prefix_containers()
        HostWorktreeEnvironment.teardown(self)

    def _seed_poetry_volume(self) -> None:
        """Пустой volume закрывает venv образа; pypi из контейнера часто недоступен."""
        dest = f'{self.prefix}_poetry_venv'
        listing = subprocess.run(
            ['docker', 'volume', 'ls', '-q'],
            capture_output=True,
            text=True,
            check=False,
        )
        names = [item.strip() for item in (listing.stdout or '').splitlines() if item.strip()]
        source = next(
            (name for name in names if name.endswith('_poetry_venv') and not name.startswith('ergo_st_')),
            '',
        )
        subprocess.run(['docker', 'volume', 'create', dest], capture_output=True, check=False)
        if source:
            subprocess.run(
                [
                    'docker',
                    'run',
                    '--rm',
                    '-v',
                    f'{source}:/src:ro',
                    '-v',
                    f'{dest}:/dst',
                    'ergo_ms-python:local',
                    'bash',
                    '-c',
                    'cp -a /src/. /dst/',
                ],
                capture_output=True,
                check=False,
                timeout=180,
            )
            return
        image = 'ergo_ms-python:local'
        subprocess.run(
            [
                'docker',
                'run',
                '--rm',
                '-v',
                f'{dest}:/seed',
                image,
                'bash',
                '-c',
                'cp -a /app/virtual_env/python/. /seed/ 2>/dev/null || true',
            ],
            capture_output=True,
            check=False,
            timeout=180,
        )

    def _store_cmd_log(self, name: str, result) -> None:
        path = self.run_dir / f'{name}.log'
        text = (result.stdout or '') + '\n--- stderr ---\n' + (result.stderr or '')
        path.write_text(text, encoding='utf-8')

    def _force_remove_prefix_containers(self) -> None:
        listing = subprocess.run(
            ['docker', 'ps', '-aq', '--filter', f'name={self.prefix}'],
            capture_output=True,
            text=True,
            check=False,
        )
        ids = [item for item in (listing.stdout or '').split() if item]
        if not ids:
            return
        subprocess.run(['docker', 'rm', '-f', *ids], capture_output=True, check=False)

    def _write_throwaway_config(self) -> None:
        ports = pick_scenario_ports()
        if not ports:
            raise RuntimeError('нет свободных портов для изолированного Docker')
        self._ports = ports
        env_text = (
            f'{PREFIX_ENV}={self.prefix}\n'
            f'DOCKER_COMPOSE_PROJECT={self.prefix}\n'
            f'COMPOSE_PROJECT_NAME={self.prefix}\n'
            'ERGO_RUNTIME=docker\n'
            'ERGO_PROXY=none\n'
            'ERGO_BROKER=redis\n'
            'ERGO_DB=postgres\n'
            'ERGO_SEARCH_ENABLED=false\n'
            'ERGO_JUPYTER=none\n'
            'MODULE_RUNTIME=monolith\n'
            'HOST_PROFILE=full\n'
            'DOCKER_MODE=dev\n'
            'DOCKER_ENABLED=true\n'
            'ERGO_SKIP_PYTHON_INSTALL=true\n'
            'ERGO_SKIP_NPM_INSTALL=true\n'
            f'API_PORT={self._ports.get("api", 18000)}\n'
            f'CLIENT_PORT={self._ports.get("client", 18001)}\n'
            f'FRONTEND_BASE_URL=http://127.0.0.1:{self._ports.get("client", 18001)}\n'
            f'CORS_ALLOWED_ORIGINS=http://127.0.0.1:{self._ports.get("client", 18001)},http://localhost:{self._ports.get("client", 18001)}\n'
            f'CSRF_TRUSTED_ORIGINS=http://127.0.0.1:{self._ports.get("client", 18001)},http://localhost:{self._ports.get("client", 18001)}\n'
            'API_ALLOWED_HOSTS=localhost,127.0.0.1,api\n'
            f'API_SECRET_KEY={secrets.token_hex(32)}\n'
            f'API_JWT_SIGNING_KEY={secrets.token_hex(32)}\n'
            'DEBUG=false\n'
            'ERGO_LOG_CONSOLE=false\n'
        )
        (self.tree_root / '.env').write_text(env_text, encoding='utf-8')
        (self.tree_root / 'databases.yaml').write_text(
            'databases:\n'
            '  default:\n'
            '    engine: postgresql\n'
            '    name: ergo_ms\n'
            '    user: postgres\n'
            '    password: admin\n'
            '    host: postgres\n'
            '    port: 5432\n',
            encoding='utf-8',
        )
