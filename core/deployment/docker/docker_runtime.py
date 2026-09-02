"""
Effective env и конфигурация для Docker Compose (read-only, не пишет .env / databases.yaml).

Порты приложений — из существующих ключей .env (API_PORT, CLIENT_PORT, …).
Параметры БД — из databases.yaml; для контейнеров генерируется .compose.databases.yaml.
Публикация postgres/meilisearch на хост — docker-compose.publish.generated.yml
(пропуск, если порт занят; bind 127.0.0.1, не 0.0.0.0; внутри сети compose: 5432/6379/7700).
Redis на хост — только при явном DOCKER_REDIS_PUBLISH_PORT (пусто = не публиковать).
AUTH Redis — docker-compose.redis-auth.generated.yml при непустом redis.password.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

_DOCKER_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _DOCKER_DIR.parent
PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from env_resolvers import load_merged_env  # noqa: E402
from ergo_modes import (  # noqa: E402
    effective_docker_profile_jupyter,
    effective_docker_profile_loadtest,
    effective_docker_profile_meilisearch,
    effective_docker_profile_postgres,
    effective_nginx_enabled,
    effective_redis_enabled,
    env_bool,
)
from project_layout import cache_dir, cache_docker_dir, ensure_dir  # noqa: E402

LOCAL_DB_HOSTS = frozenset({'localhost', '127.0.0.1', '::1', ''})
CELERY_DB_SECTIONS = ('default', 'celery', 'celery_worker', 'celery_beat')
CELERY_BALANCE_KEYS = (
    'CELERY_BALANCE',
    'CELERY_BALANCE_OS_RESERVE_RAM_MB',
    'CELERY_BALANCE_RESERVE_CPU',
    'CELERY_BALANCE_MIN_CONCURRENCY',
    'CELERY_BALANCE_MAX_CONCURRENCY',
    'CELERY_BALANCE_GPU',
    'CELERY_BALANCE_WATCH_INTERVAL',
    'CELERY_BALANCE_HYSTERESIS',
)
DOCKER_DEPS_CACHE_VALUES = frozenset({'internal', 'project', 'off'})
BUILD_CACHE_OUTPUT = _DOCKER_DIR / 'docker-compose.build.generated.yml'
PUBLISH_COMPOSE_OUTPUT = _DOCKER_DIR / 'docker-compose.publish.generated.yml'
REDIS_AUTH_COMPOSE_OUTPUT = _DOCKER_DIR / 'docker-compose.redis-auth.generated.yml'


def docker_home(project_root: Path | None = None) -> Path:
    """Каталог compose выбранного корня, а не каталог этого файла."""
    if project_root is not None:
        candidate = Path(project_root).resolve() / 'core' / 'deployment' / 'docker'
        if candidate.is_dir():
            return candidate
    return _DOCKER_DIR.resolve()
# Infra-порты на хост только с loopback — не с LAN.
INFRA_PUBLISH_BIND = '127.0.0.1'
_PUBLISH_DISABLED = frozenset({'none', 'off', 'false', '0', '-', 'disabled'})
_PUBLISH_WARNED: set[str] = set()


def _yaml():
    """PyYAML нужен только для Docker-артефактов; setup-full на системном Python без него."""
    import yaml  # noqa: WPS433

    return yaml


def _env(raw: dict[str, str], name: str, default: str = '') -> str:
    return raw.get(name, default).strip() or default


def _docker_image_exists(image_ref: str) -> bool:
    if not image_ref.strip():
        return False
    result = subprocess.run(
        ['docker', 'image', 'inspect', image_ref],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def resolve_docker_python_base(raw: dict[str, str] | None = None) -> str:
    """База Dockerfile.python: slim, иначе уже собранный python-образ."""
    explicit = _env(raw or {}, 'DOCKER_PYTHON_BASE', '')
    if explicit:
        return explicit
    preferred = 'python:3.12-slim-bookworm'
    local_python = _env(raw or {}, 'DOCKER_PYTHON_IMAGE', 'ergo_ms-python:local') or 'ergo_ms-python:local'
    if _docker_image_exists(local_python):
        return local_python
    return preferred


def resolve_docker_node_base(raw: dict[str, str] | None = None) -> str:
    """База Dockerfile.client: официальный node, иначе уже собранный client."""
    explicit = _env(raw or {}, 'DOCKER_NODE_BASE', '')
    if explicit:
        return explicit
    preferred = 'node:20-bookworm-slim'
    if _docker_image_exists(preferred):
        return preferred
    fallback = _env(raw or {}, 'DOCKER_NODE_IMAGE', 'ergo_ms-client:local') or 'ergo_ms-client:local'
    if _docker_image_exists(fallback):
        return fallback
    return preferred


def load_databases_config(project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or PROJECT_ROOT
    path = root / 'databases.yaml'
    if not path.is_file():
        example = root / 'databases.yaml.example'
        path = example if example.is_file() else path
    if not path.is_file():
        return {}
    with open(path, encoding='utf-8') as handle:
        data = _yaml().safe_load(handle) or {}
    return data.get('databases') or {}


def load_redis_password(project_root: Path | None = None) -> str:
    """databases.yaml → redis.password; пусто = без AUTH."""
    section = load_databases_config(project_root).get('redis') or {}
    if not isinstance(section, dict):
        return ''
    raw = section.get('password', '')
    if raw is None:
        return ''
    return str(raw).strip()


def load_redis_user(project_root: Path | None = None) -> str:
    """databases.yaml → redis.user; пусто = default / requirepass."""
    section = load_databases_config(project_root).get('redis') or {}
    if not isinstance(section, dict):
        return ''
    raw = section.get('user', '')
    if raw is None:
        return ''
    return str(raw).strip()


def effective_db_host(raw_env: dict[str, str], yaml_host: str) -> str:
    mode = _env(raw_env, 'DOCKER_DATABASE', 'container').lower()
    service = _env(raw_env, 'DOCKER_SERVICE_POSTGRES', 'postgres')
    host = (yaml_host or '').strip()
    if mode == 'container' and host.lower() in LOCAL_DB_HOSTS:
        return service
    return host or 'localhost'


def effective_redis_compose_host(raw_env: dict[str, str], yaml_host: str) -> str:
    """localhost в yaml → имя сервиса Redis в сети compose."""
    service = _env(raw_env, 'DOCKER_SERVICE_REDIS', 'redis')
    host = (yaml_host or '').strip()
    if host.lower() in LOCAL_DB_HOSTS:
        return service
    return host or service


def build_compose_databases(project_root: Path | None, raw_env: dict[str, str]) -> dict[str, Any]:
    sections = load_databases_config(project_root)
    if not sections:
        return {}
    result = deepcopy(sections)
    for name in CELERY_DB_SECTIONS:
        section = result.get(name)
        if not isinstance(section, dict):
            continue
        section['host'] = effective_db_host(raw_env, str(section.get('host', '')))
    redis_section = result.get('redis')
    if isinstance(redis_section, dict):
        redis_section['host'] = effective_redis_compose_host(
            raw_env,
            str(redis_section.get('host', '')),
        )
    return result


def write_compose_databases(path: Path, databases: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'databases': databases}
    path.write_text(
        _yaml().safe_dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding='utf-8',
    )


def build_compose_databases_loadtest(
    project_root: Path | None,
    raw_env: dict[str, str],
) -> dict[str, Any]:
    """databases.yaml для api-loadtest: host=postgres-loadtest, name=*_loadtest."""
    base = build_compose_databases(project_root, raw_env)
    if not base:
        return {}
    result = deepcopy(base)
    default = result.get('default')
    if isinstance(default, dict):
        src_name = str(default.get('name') or 'ergo_ms').strip() or 'ergo_ms'
        loadtest_name = _env(raw_env, 'LOADTEST_POSTGRES_DB', f'{src_name}_loadtest')
        default['host'] = 'postgres-loadtest'
        default['name'] = loadtest_name
        default['port'] = 5432
    redis_section = result.get('redis')
    if isinstance(redis_section, dict):
        redis_section['host'] = effective_redis_compose_host(
            raw_env,
            str(redis_section.get('host', '')),
        )
    return result


def effective_docker_deps_cache(raw_env: dict[str, str]) -> str:
    mode = _env(raw_env, 'DOCKER_DEPS_CACHE', 'internal').lower()
    return mode if mode in DOCKER_DEPS_CACHE_VALUES else 'internal'


def effective_docker_build_policy(raw_env: dict[str, str]) -> str:
    policy = _env(raw_env, 'DOCKER_BUILD_POLICY', 'if-missing').lower()
    return policy if policy in ('if-missing', 'always') else 'if-missing'


def effective_docker_npm_install(raw_env: dict[str, str]) -> str:
    mode = _env(raw_env, 'DOCKER_NPM_INSTALL', 'smart').lower()
    return mode if mode in ('smart', 'always') else 'smart'


def resolve_celery_cache_bind(project_root: Path, raw_env: dict[str, str]) -> str:
    mode = _env(raw_env, 'DOCKER_VOLUME_CELERY_CACHE', 'named').lower()
    if mode == 'bind':
        cache_path = ensure_dir(cache_dir(project_root)).resolve()
        return str(cache_path).replace('\\', '/')
    return 'celery_cache'


def resolve_docker_cache_dir(project_root: Path) -> str:
    docker_cache = ensure_dir(cache_docker_dir(project_root)).resolve()
    return str(docker_cache).replace('\\', '/')


def build_compose_build_cache_content(cache_dir: str) -> str:
    """Фрагмент compose для project-кэша BuildKit (local cache)."""
    api_cache = f'{cache_dir}/build-api'
    return f"""# Автогенерация: prepare_compose_artifacts (DOCKER_DEPS_CACHE=project)
services:
  api:
    build:
      cache_from:
        - type=local,src={api_cache}
      cache_to:
        - type=local,dest={api_cache},mode=max
"""


def write_compose_build_cache(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def remove_compose_build_cache(path: Path | None = None) -> None:
    target = path or BUILD_CACHE_OUTPUT
    if target.is_file():
        target.unlink()


def build_compose_env_overrides(raw_env: dict[str, str]) -> dict[str, str]:
    """Runtime-overrides для .compose.env (не дублирует порты — они уже в .env)."""
    overrides: dict[str, str] = {
        'DOCKER_ENABLED': 'true',
        'ERGO_RUNTIME': 'docker',
        'REDIS_HOST': _env(raw_env, 'DOCKER_SERVICE_REDIS', 'redis'),
    }
    if effective_redis_enabled(raw_env) or env_bool(raw_env.get('REDIS_ENABLED'), default=True):
        overrides['REDIS_ENABLED'] = 'true'
        overrides['ERGO_BROKER'] = raw_env.get('ERGO_BROKER') or 'redis'

    service_api = _env(raw_env, 'DOCKER_SERVICE_API', 'api')
    service_media = _env(raw_env, 'DOCKER_SERVICE_MEDIA', 'media-api')

    # Bind внутри контейнера (слушать все интерфейсы). Браузер на 0.0.0.0
    # ходить не может — см. CLIENT_API_HOST ниже.
    overrides['API_HOST'] = '0.0.0.0'
    overrides['MEDIA_API_BIND_HOST'] = '0.0.0.0'
    overrides['CLIENT_HOST'] = '0.0.0.0'
    if effective_docker_profile_jupyter(raw_env):
        # Внутри контейнера слушать сеть compose; на хост — только 127.0.0.1.
        overrides.setdefault('API_JUPYTER_BIND_HOST', '0.0.0.0')

    # Публичный хост API для SPA (Vite define CLIENT_API_HOST). Берём из .env
    # до override bind; 0.0.0.0/* заменяем на localhost.
    browser_api_host = _env(raw_env, 'CLIENT_API_HOST', '') or _env(raw_env, 'API_HOST', 'localhost')
    if browser_api_host in ('0.0.0.0', '*', '::', '[::]'):
        browser_api_host = 'localhost'
    overrides['CLIENT_API_HOST'] = browser_api_host

    if env_bool(raw_env.get('DOCKER_PROFILE_NGINX')) or effective_nginx_enabled(raw_env):
        overrides['NGINX_ENABLED'] = 'true'
        overrides['ERGO_PROXY'] = 'nginx'
        overrides['CLIENT_USE_RELATIVE_API'] = 'true'

    mode = _env(raw_env, 'DOCKER_MODE', 'dev').lower()
    if mode == 'prod':
        overrides.setdefault('ERGO_ENV', 'production')
    else:
        overrides.setdefault('ERGO_ENV', 'development')

    # Для healthcheck / wait — хост БД в сети compose.
    # В контейнере Postgres слушает 5432; порт из databases.yaml — публикация на хост.
    default_db = load_databases_config().get('default') or {}
    overrides['ERGO_DOCKER_DB_HOST'] = effective_db_host(raw_env, str(default_db.get('host', '')))
    db_mode = _env(raw_env, 'DOCKER_DATABASE', 'container').lower()
    if db_mode == 'container':
        overrides['ERGO_DOCKER_DB_PORT'] = '5432'
    else:
        overrides['ERGO_DOCKER_DB_PORT'] = str(default_db.get('port', 5432))
    overrides['ERGO_DOCKER_SERVICE_API'] = service_api
    overrides['ERGO_DOCKER_SERVICE_MEDIA'] = service_media

    media_volume = _env(raw_env, 'DOCKER_VOLUME_MEDIA', 'bind').lower()
    overrides.setdefault('MEDIA_STORAGE_PATH', '/app/media')

    overrides.setdefault('DOCKER_BUILD_CACHE', 'true' if env_bool(raw_env.get('DOCKER_BUILD_CACHE'), default=True) else 'false')
    deps_cache = effective_docker_deps_cache(raw_env)
    if not env_bool(raw_env.get('DOCKER_BUILD_CACHE'), default=True):
        deps_cache = 'off'
    overrides.setdefault('DOCKER_DEPS_CACHE', deps_cache)
    overrides.setdefault('DOCKER_BUILD_POLICY', effective_docker_build_policy(raw_env))
    overrides.setdefault('DOCKER_NPM_INSTALL', effective_docker_npm_install(raw_env))
    overrides.setdefault('ERGO_DOCKER_LOG_DIR', '/app/logs/docker')
    overrides.setdefault('ERGO_DOCKER_SETUP_MARKER', '/app/logs/.ergo-docker-setup-ok')
    overrides.setdefault('PIP_DEFAULT_TIMEOUT', '300')
    overrides.setdefault('PIP_RETRIES', '10')

    # Module microservices (MODULE_RUNTIME=microservice): URL-карта для HttpTransport.
    runtime = _env(raw_env, 'MODULE_RUNTIME', 'monolith').lower()
    if runtime in ('microservice', 'split'):
        api_port = _env(raw_env, 'API_PORT', '8000')
        overrides.setdefault(
            'BRIDGE_CORE_URL',
            f'http://{service_api}:{api_port}',
        )
        if not _env(raw_env, 'BRIDGE_SERVICE_URLS', ''):
            modules_raw = (
                _env(raw_env, 'MICROSERVICE_MODULES', '')
            )
            ms_modules = [m.strip() for m in modules_raw.split(',') if m.strip()]
            parts: list[str] = []
            for name in ms_modules:
                key = name.upper().replace('-', '_')
                port = _env(raw_env, f'{key}_PORT', '')
                if not port:
                    port = str(8100 + (sum(ord(c) for c in name) % 500))
                parts.append(f'{name}=http://{name}:{port}')
            if parts:
                overrides['BRIDGE_SERVICE_URLS'] = ','.join(parts)
        # В Docker microservice HTTP/Redis — иначе модули не видят друг друга.
        transport = _env(raw_env, 'BRIDGE_TRANSPORT', 'http')
        overrides['BRIDGE_TRANSPORT'] = transport if transport != 'local' else 'http'
        event_bus = _env(raw_env, 'BRIDGE_EVENT_BUS', 'redis')
        overrides['BRIDGE_EVENT_BUS'] = event_bus if event_bus != 'local' else 'redis'

    # Overlay celery-balance читает те же knobs, что и хост (том virtual_env/cache).
    for key in CELERY_BALANCE_KEYS:
        value = _env(raw_env, key, '')
        if value:
            overrides[key] = value

    return overrides


def merge_env_files(project_root: Path, raw_env: dict[str, str]) -> dict[str, str]:
    merged = dict(raw_env)
    merged.update(build_compose_env_overrides(raw_env))
    return merged


def write_compose_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{key}={value}' for key, value in sorted(values.items())]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def docker_mode(raw_env: dict[str, str]) -> str:
    mode = _env(raw_env, 'DOCKER_MODE', 'dev').lower()
    return mode if mode in ('dev', 'prod') else 'dev'


def compose_profiles(raw_env: dict[str, str]) -> list[str]:
    profiles: list[str] = []
    from lifecycle.host_profile import resolve_host_profile

    host = resolve_host_profile(raw_env)
    profiles.extend(host.docker_compose_profiles())
    if env_bool(raw_env.get('DOCKER_PROFILE_NGINX')) or effective_nginx_enabled(raw_env):
        profiles.append('nginx')
    if effective_docker_profile_jupyter(raw_env):
        profiles.append('jupyter')
    db_mode = _env(raw_env, 'DOCKER_DATABASE', 'container').lower()
    if db_mode == 'container' and effective_docker_profile_postgres(raw_env):
        profiles.append('postgres')
    if effective_docker_profile_loadtest(raw_env):
        profiles.append('loadtest')
    if effective_docker_profile_meilisearch(raw_env):
        profiles.append('meilisearch')
    return profiles


def host_tcp_port_available(port: int) -> bool:
    """True, если порт свободен для публикации Docker на хост (0.0.0.0 и 127.0.0.1)."""
    if port <= 0 or port > 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.35)
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('0.0.0.0', port))
        except OSError:
            return False
    return True


_DOCKER_PROCESS_MARKERS = ('docker', 'com.docker', 'vpnkit', 'wslrelay')


def _windows_listening_pids(port: int, *, loopback_only: bool = False) -> set[int]:
    """PID процессов, слушающих TCP-порт (Windows netstat)."""
    try:
        result = subprocess.run(
            ['netstat', '-ano', '-p', 'tcp'],
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    # netstat на Windows часто в OEM/cp866 — не utf-8
    raw = result.stdout or b''
    for encoding in ('oem', 'cp866', 'cp1251', 'utf-8'):
        try:
            text = raw.decode(encoding)
            break
        except (LookupError, UnicodeDecodeError):
            text = raw.decode('utf-8', errors='replace')
    pids: set[int] = set()
    for line in text.splitlines():
        if 'LISTENING' not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        if local_addr.startswith('['):
            host_part, _, port_part = local_addr[1:].partition(']:')
        else:
            host_part, _, port_part = local_addr.rpartition(':')
        if port_part != str(port):
            continue
        host_part = host_part.strip('[]')
        if loopback_only and host_part not in ('127.0.0.1', '::1'):
            continue
        try:
            pids.add(int(parts[-1]))
        except ValueError:
            continue
    return pids


def _windows_process_name(pid: int) -> str:
    try:
        result = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    raw = result.stdout or b''
    for encoding in ('oem', 'cp866', 'cp1251', 'utf-8'):
        try:
            text = raw.decode(encoding)
            break
        except (LookupError, UnicodeDecodeError):
            text = raw.decode('utf-8', errors='replace')
    line = text.strip().splitlines()
    if not line:
        return ''
    # "Cursor.exe","26120",...
    first = line[0].strip().strip('"').split('","')[0].strip('"')
    return first.lower()


def _is_docker_related_process(name: str) -> bool:
    lowered = (name or '').lower()
    return any(marker in lowered for marker in _DOCKER_PROCESS_MARKERS)


def foreign_loopback_blocks_localhost(port: int) -> bool:
    """
    True, если на 127.0.0.1/::1 порт занят не-Docker процессом (Cursor port-forward и т.п.).

    Браузер на localhost:PORT попадёт туда, а не в Docker publish на 0.0.0.0:PORT —
    типичный «CORS без заголовков» / Network Error при живом API в контейнере.
    """
    if port <= 0 or port > 65535:
        return False
    if sys.platform.startswith('win'):
        pids = _windows_listening_pids(port, loopback_only=True)
        for pid in pids:
            name = _windows_process_name(pid)
            if name and not _is_docker_related_process(name):
                return True
        return False
    # Linux/macOS: отдельный bind на 127.0.0.1 при занятом loopback
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            return True
    return False


def kill_foreign_loopback_listeners(port: int, *, env_key: str = '', warn: bool = True) -> bool:
    """
    Завершить не-Docker процессы на 127.0.0.1/::1:port (Cursor port-forward и т.п.).

    Возвращает True, если после попытки loopback больше не блокирует порт.
    """
    if not foreign_loopback_blocks_localhost(port):
        return True
    if not sys.platform.startswith('win'):
        if warn:
            warn_key = f'app-kill-unsupported:{env_key}:{port}'
            if warn_key not in _PUBLISH_WARNED:
                _PUBLISH_WARNED.add(warn_key)
                print(
                    format_console(
                        'warning',
                        t(
                            'docker_app_port_kill_unsupported',
                            env_key=env_key or 'PORT',
                            port=port,
                        ),
                    )
                )
        return False

    killed: list[str] = []
    for pid in sorted(_windows_listening_pids(port, loopback_only=True)):
        name = _windows_process_name(pid) or f'pid:{pid}'
        if _is_docker_related_process(name):
            continue
        try:
            result = subprocess.run(
                ['taskkill', '/PID', str(pid), '/F'],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            killed.append(f'{name}({pid})')

    if killed:
        time.sleep(0.6)
        if warn:
            warn_key = f'app-kill:{env_key}:{port}:{",".join(killed)}'
            if warn_key not in _PUBLISH_WARNED:
                _PUBLISH_WARNED.add(warn_key)
                print(
                    format_console(
                        'warning',
                        t(
                            'docker_app_port_killed',
                            env_key=env_key or 'PORT',
                            port=port,
                            processes=', '.join(killed),
                        ),
                    )
                )
    return not foreign_loopback_blocks_localhost(port)


def resolve_docker_app_port(preferred: int, *, env_key: str, warn: bool = True) -> int:
    """Порт API/client для publish; конфликт loopback с IDE — убиваем процесс, порт не меняем."""
    if preferred <= 0 or preferred > 65535:
        preferred = 8000 if 'API' in env_key.upper() else preferred
    if foreign_loopback_blocks_localhost(preferred):
        kill_foreign_loopback_listeners(preferred, env_key=env_key, warn=warn)
    if not foreign_loopback_blocks_localhost(preferred) and host_tcp_port_available(preferred):
        return preferred
    if not foreign_loopback_blocks_localhost(preferred):
        # Порт занят самим Docker / уже опубликован — не переназначаем.
        return preferred
    if warn:
        warn_key = f'app-still-blocked:{env_key}:{preferred}'
        if warn_key not in _PUBLISH_WARNED:
            _PUBLISH_WARNED.add(warn_key)
            print(
                format_console(
                    'warning',
                    t(
                        'docker_app_port_still_blocked',
                        env_key=env_key,
                        port=preferred,
                    ),
                )
            )
    return preferred


def _parse_publish_port_explicit(raw: str) -> int | None | str:
    """int — явный порт; None — не публиковать; 'auto' — выбрать автоматически."""
    value = (raw or '').strip().lower()
    if not value:
        return 'auto'
    if value in _PUBLISH_DISABLED:
        return None
    try:
        port = int(value)
    except ValueError:
        return None
    if port <= 0 or port > 65535:
        return None
    return port


def resolve_infra_host_publish_port(
    *,
    preferred: int,
    explicit_raw: str,
    service_key: str,
    warn: bool = False,
) -> int | None:
    """
    Порт на хост для infra-сервиса или None (только сеть compose).

    Явный DOCKER_*_PUBLISH_PORT / POSTGRES_PUBLISH_PORT;
    none/off — не публиковать; пусто — опубликовать preferred, если свободен.
    """
    parsed = _parse_publish_port_explicit(explicit_raw)
    if parsed is None:
        return None
    if parsed != 'auto':
        return int(parsed)
    if host_tcp_port_available(preferred):
        return preferred
    if warn:
        warn_key = f'{service_key}:{preferred}'
        if warn_key not in _PUBLISH_WARNED:
            _PUBLISH_WARNED.add(warn_key)
            print(
                format_console(
                    'warning',
                    t(
                        'docker_publish_port_busy_skip',
                        service=service_key,
                        port=preferred,
                    ),
                )
            )
    return None


def resolve_infra_publish_ports(raw_env: dict[str, str], *, warn: bool = False) -> dict[str, int]:
    """Имя compose-сервиса → порт на хосте (только для свободных/явных)."""
    published: dict[str, int] = {}
    redis_service = _env(raw_env, 'DOCKER_SERVICE_REDIS', 'redis')
    redis_internal = int(_env(raw_env, 'REDIS_PORT', '6379') or '6379')
    redis_explicit = _env(raw_env, 'DOCKER_REDIS_PUBLISH_PORT', '')
    # Redis: пустой DOCKER_REDIS_PUBLISH_PORT = не публиковать (opt-in).
    if redis_explicit:
        redis_host = resolve_infra_host_publish_port(
            preferred=redis_internal,
            explicit_raw=redis_explicit,
            service_key=redis_service,
            warn=warn,
        )
        if redis_host is not None:
            published[redis_service] = redis_host

    db_mode = _env(raw_env, 'DOCKER_DATABASE', 'container').lower()
    if db_mode == 'container' and effective_docker_profile_postgres(raw_env):
        default_db = load_databases_config().get('default') or {}
        preferred = int(default_db.get('port', 5432) or 5432)
        pg_service = _env(raw_env, 'DOCKER_SERVICE_POSTGRES', 'postgres')
        explicit = (
            _env(raw_env, 'DOCKER_POSTGRES_PUBLISH_PORT', '')
            or _env(raw_env, 'POSTGRES_PUBLISH_PORT', '')
        )
        pg_host = resolve_infra_host_publish_port(
            preferred=preferred,
            explicit_raw=explicit,
            service_key=pg_service,
            warn=warn,
        )
        if pg_host is not None:
            published[pg_service] = pg_host

    if effective_docker_profile_meilisearch(raw_env):
        meili_service = _env(raw_env, 'DOCKER_SERVICE_MEILISEARCH', 'meilisearch')
        meili_preferred = int(_env(raw_env, 'MEILI_PUBLISH_PORT', '8004') or '8004')
        # Host MEILI_HTTP_ADDR / MEILI_HOST часто 8004 — предпочитаем тот же порт на хост.
        http_addr = _env(raw_env, 'MEILI_HTTP_ADDR', '')
        if ':' in http_addr:
            try:
                meili_preferred = int(http_addr.rsplit(':', 1)[-1].strip() or meili_preferred)
            except ValueError:
                pass
        meili_explicit = _env(raw_env, 'DOCKER_MEILI_PUBLISH_PORT', '')
        meili_host = resolve_infra_host_publish_port(
            preferred=meili_preferred,
            explicit_raw=meili_explicit,
            service_key=meili_service,
            warn=warn,
        )
        if meili_host is not None:
            published[meili_service] = meili_host
    return published


def build_publish_compose_content(published: dict[str, int]) -> str:
    lines = [
        '# Автогенерация: prepare_compose_artifacts',
        '# Публикация postgres/redis/meilisearch на 127.0.0.1 (redis — только при явном порте).',
        '# Внутри сети compose: postgres:5432, redis:6379, meilisearch:7700.',
    ]
    if not published:
        lines.extend(['services: {}', ''])
        return '\n'.join(lines)
    lines.append('services:')
    container_ports = {
        'postgres': 5432,
        'redis': 6379,
        'meilisearch': 7700,
    }
    for service, host_port in sorted(published.items()):
        container_port = container_ports.get(service, host_port)
        lines.append(f'  {service}:')
        lines.append('    ports:')
        lines.append(
            f'      - "{INFRA_PUBLISH_BIND}:{host_port}:{container_port}"'
        )
    lines.append('')
    return '\n'.join(lines)


def write_publish_compose(path: Path, published: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_publish_compose_content(published), encoding='utf-8')


def _yaml_double_quoted(value: str) -> str:
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def build_redis_auth_compose_content(password: str, username: str = '') -> str:
    """Overlay: requirepass (+ ACL user) и healthcheck с AUTH."""
    quoted_pass = _yaml_double_quoted(password)
    command = ['redis-server', '--requirepass', password]
    if username:
        command.extend(['--user', username, 'on', f'>{password}', '~*', '&*', '+@all'])
    command_yaml = ', '.join(_yaml_double_quoted(item) for item in command)
    return (
        '# Автогенерация: prepare_compose_artifacts (databases.yaml redis.password)\n'
        'services:\n'
        '  redis:\n'
        f'    command: [{command_yaml}]\n'
        '    healthcheck:\n'
        f'      test: ["CMD", "redis-cli", "-a", {quoted_pass}, "--no-auth-warning", "ping"]\n'
        '      interval: 5s\n'
        '      timeout: 3s\n'
        '      retries: 10\n'
    )


def write_redis_auth_compose(path: Path, password: str, username: str = '') -> None:
    """Пишет AUTH-overlay или удаляет файл, если пароль пуст."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not password:
        path.unlink(missing_ok=True)
        return
    path.write_text(build_redis_auth_compose_content(password, username), encoding='utf-8')


def postgres_container_env(raw_env: dict[str, str]) -> dict[str, str]:
    default_db = load_databases_config().get('default') or {}
    user = str(default_db.get('user') or 'postgres').strip() or 'postgres'
    password = str(default_db.get('password') or 'admin').strip() or 'admin'
    name = str(default_db.get('name') or 'ergo_ms').strip() or 'ergo_ms'
    return {
        'POSTGRES_USER': user,
        'POSTGRES_PASSWORD': password,
        'POSTGRES_DB': name,
    }


def postgres_publish_port(raw_env: dict[str, str]) -> str:
    """Обратная совместимость: порт публикации или '' если на хост не публикуем."""
    published = resolve_infra_publish_ports(raw_env, warn=False)
    pg_service = _env(raw_env, 'DOCKER_SERVICE_POSTGRES', 'postgres')
    if pg_service in published:
        return str(published[pg_service])
    return ''


def generate_celery_init_sql(project_root: Path | None = None) -> str:
    """SQL для init postgres: дополнительные БД Celery из databases.yaml."""
    sections = load_databases_config(project_root)
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for name in ('celery', 'celery_worker', 'celery_beat'):
        section = sections.get(name)
        if not isinstance(section, dict):
            continue
        db_name = str(section.get('name', '')).strip()
        db_user = str(section.get('user', '')).strip()
        db_pass = str(section.get('password', '')).strip()
        if not db_name or not db_user or not db_pass:
            continue
        key = (db_user, db_name)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"DO $$ BEGIN CREATE USER {db_user} WITH PASSWORD '{db_pass}'; EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
        lines.append(
            f"DO $$ BEGIN CREATE DATABASE {db_name} OWNER {db_user}; EXCEPTION WHEN duplicate_database THEN NULL; END $$;"
        )
        lines.append(f'GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user};')
    return '\n'.join(lines) + ('\n' if lines else '')


def write_celery_init_sql(path: Path, project_root: Path | None = None) -> None:
    content = generate_celery_init_sql(project_root)
    if not content.strip():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def resolve_volume_binds(project_root: Path, raw_env: dict[str, str]) -> dict[str, str]:
    logs_mode = _env(raw_env, 'DOCKER_VOLUME_LOGS', 'bind').lower()
    media_mode = _env(raw_env, 'DOCKER_VOLUME_MEDIA', 'bind').lower()
    binds: dict[str, str] = {
        'ERGO_PROJECT_ROOT': str(project_root.resolve()).replace('\\', '/'),
        'ERGO_CELERY_CACHE_BIND': resolve_celery_cache_bind(project_root, raw_env),
    }
    if logs_mode == 'bind':
        logs_dir = (project_root / 'logs').resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / 'docker').mkdir(parents=True, exist_ok=True)
        binds['ERGO_LOGS_BIND'] = str(logs_dir).replace('\\', '/')
    else:
        binds['ERGO_LOGS_BIND'] = 'ergo_logs'
    if media_mode == 'bind':
        binds['ERGO_MEDIA_BIND'] = str((project_root / 'media').resolve()).replace('\\', '/')
    else:
        binds['ERGO_MEDIA_BIND'] = 'ergo_media'
    return binds


def prepare_compose_artifacts(
    project_root: Path | None = None,
    *,
    resolve_app_ports: bool = True,
    warn_image_bases: bool = False,
) -> dict[str, Path]:
    root = (project_root or PROJECT_ROOT).resolve()
    raw = dict(load_merged_env(root))
    for key, value in os.environ.items():
        if not value or not str(value).strip():
            continue
        if key.startswith('DOCKER_PROFILE_') or key.startswith('LOADTEST_'):
            raw[key] = value
    home = docker_home(root)
    compose_env_path = home / '.compose.env'
    compose_db_path = home / '.compose.databases.yaml'
    publish_path = home / 'docker-compose.publish.generated.yml'
    redis_auth_path = home / 'docker-compose.redis-auth.generated.yml'
    build_cache_path = home / 'docker-compose.build.generated.yml'

    published = resolve_infra_publish_ports(raw, warn=True)
    write_publish_compose(publish_path, published)

    redis_password = load_redis_password(root)
    redis_user = load_redis_user(root)
    write_redis_auth_compose(redis_auth_path, redis_password, redis_user)

    merged = merge_env_files(root, raw)
    merged.update(postgres_container_env(raw))
    pg_service = _env(raw, 'DOCKER_SERVICE_POSTGRES', 'postgres')
    if pg_service in published:
        merged['POSTGRES_PUBLISH_PORT'] = str(published[pg_service])
    else:
        merged.pop('POSTGRES_PUBLISH_PORT', None)

    # Cursor/IDE на 127.0.0.1:8000 перехватывает localhost у Docker → «CORS Network Error».
    # Loadtest публикует LOADTEST_API_PORT, не API_PORT — хостовый API на 8000 не трогаем.
    # docker-build / ps не публикуют порты — процессы на 8000 не завершаем.
    if resolve_app_ports and not effective_docker_profile_loadtest(
        merged
    ) and not effective_docker_profile_loadtest(raw):
        api_preferred = int(_env(merged, 'API_PORT', '8000') or '8000')
        client_preferred = int(_env(merged, 'CLIENT_PORT', '8001') or '8001')
        merged['API_PORT'] = str(resolve_docker_app_port(api_preferred, env_key='API_PORT', warn=True))
        merged['CLIENT_PORT'] = str(
            resolve_docker_app_port(client_preferred, env_key='CLIENT_PORT', warn=True)
        )

    node_base = resolve_docker_node_base(merged)
    merged['DOCKER_NODE_BASE'] = node_base
    if warn_image_bases and node_base != 'node:20-bookworm-slim':
        warn_key = f'node-base:{node_base}'
        if warn_key not in _PUBLISH_WARNED:
            _PUBLISH_WARNED.add(warn_key)
            print(
                format_console(
                    'warning',
                    t('docker_node_base_fallback', image=node_base),
                )
            )

    python_base = resolve_docker_python_base(merged)
    merged['DOCKER_PYTHON_BASE'] = python_base
    if warn_image_bases and python_base not in ('python:3.12-slim-bookworm', 'python:3.12-slim'):
        warn_key = f'python-base:{python_base}'
        if warn_key not in _PUBLISH_WARNED:
            _PUBLISH_WARNED.add(warn_key)
            print(
                format_console(
                    'warning',
                    t('docker_python_base_fallback', image=python_base),
                )
            )

    binds = resolve_volume_binds(root, raw)
    merged.update(binds)

    databases = build_compose_databases(root, raw)
    if databases:
        write_compose_databases(compose_db_path, databases)

    compose_db_loadtest_path = home / '.compose.databases.loadtest.yaml'
    loadtest_dbs = build_compose_databases_loadtest(root, raw)
    if loadtest_dbs:
        write_compose_databases(compose_db_loadtest_path, loadtest_dbs)

    # Порты ephemeral loadtest API / published postgres-loadtest (host provision).
    merged.setdefault('LOADTEST_API_PORT', _env(raw, 'LOADTEST_API_PORT', '18000'))
    merged.setdefault('LOADTEST_POSTGRES_PORT', _env(raw, 'LOADTEST_POSTGRES_PORT', '15432'))
    if isinstance(loadtest_dbs.get('default'), dict):
        merged.setdefault(
            'LOADTEST_POSTGRES_DB',
            str(loadtest_dbs['default'].get('name') or 'ergo_ms_loadtest'),
        )
    write_compose_env(compose_env_path, merged)

    celery_sql = home / 'init' / 'postgres' / '02-celery-databases.sql'
    write_celery_init_sql(celery_sql, root)

    if effective_docker_deps_cache(raw) == 'project':
        cache_dir = resolve_docker_cache_dir(root)
        write_compose_build_cache(
            build_cache_path,
            build_compose_build_cache_content(cache_dir),
        )
    else:
        remove_compose_build_cache(build_cache_path)

    if str(_DEPLOYMENT_DIR) not in sys.path:
        sys.path.insert(0, str(_DEPLOYMENT_DIR))
    from lifecycle.docker.ignore import sync_dockerfile_dockerignore  # noqa: E402
    from lifecycle.modules.catalog import ModuleCatalog  # noqa: E402

    sync_dockerfile_dockerignore(root, ModuleCatalog.from_env(root, raw))

    return {
        'compose_env': compose_env_path,
        'compose_databases': compose_db_path,
        'celery_init_sql': celery_sql,
        'compose_build_cache': build_cache_path if build_cache_path.is_file() else None,
        'compose_publish': publish_path,
        'compose_redis_auth': redis_auth_path if redis_auth_path.is_file() else None,
    }
