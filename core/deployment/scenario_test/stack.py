"""Сборка изолированного compose-проекта во временном каталоге."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from generate_modules_compose import generate
from lifecycle.modules.catalog import ModuleCatalog
from render_common import render_docker_nginx_config

COMPOSE_PROJECT = 'ergo_ms_scenario'
PYTHON_IMAGE = 'ergo_ms-python:local'
POSTGRES_IMAGE = 'postgres:16-alpine'
REDIS_IMAGE = 'redis:7-alpine'
NGINX_IMAGE = 'nginx:1.27-alpine'
MEILI_IMAGE = 'getmeili/meilisearch:v1.43.1'
MYSQL_IMAGE = 'mysql:8'
MSSQL_IMAGE = 'mcr.microsoft.com/mssql/server:2022-latest'
RUNTIME_ENV_NAME = '.env'
SCENARIO_DB_NAME = 'ergo_ms_scenario'
SCENARIO_MYSQL_PASSWORD = 'admin'
SCENARIO_MSSQL_PASSWORD = 'Scenario_Adm1n!'


def posix(path: Path) -> str:
    return str(path.resolve()).replace('\\', '/')


def modules_with_bridge(project_root: Path) -> list[str]:
    catalog = ModuleCatalog.from_env(project_root)
    names: list[str] = []
    for name in catalog.enabled_names():
        manifest = project_root / 'modules' / name / 'api' / 'bridge_manifest.yaml'
        if manifest.is_file():
            names.append(name)
    return names


def write_databases_yaml(
    path: Path,
    *,
    db: str = 'postgres',
    db_host: str = 'postgres',
    db_port: int = 5432,
    redis_host: str = 'redis',
    redis_port: int = 6379,
    sqlite_path: Path | None = None,
) -> None:
    if db == 'sqlite':
        raw_sqlite = sqlite_path or (path.parent / 'scenario.sqlite3')
        sqlite = str(raw_sqlite).replace('\\', '/')
        if not sqlite.startswith('/'):
            sqlite = posix(Path(raw_sqlite))
        default_block = (
            '  default:\n'
            '    engine: sqlite\n'
            f'    name: {sqlite}\n'
        )
    elif db == 'mysql':
        default_block = (
            '  default:\n'
            '    engine: mysql\n'
            f'    name: {SCENARIO_DB_NAME}\n'
            '    user: root\n'
            f'    password: {SCENARIO_MYSQL_PASSWORD}\n'
            f'    host: {db_host}\n'
            f'    port: {int(db_port)}\n'
        )
    elif db == 'mssql':
        default_block = (
            '  default:\n'
            '    engine: mssql\n'
            f'    name: {SCENARIO_DB_NAME}\n'
            '    user: sa\n'
            f'    password: {SCENARIO_MSSQL_PASSWORD}\n'
            f'    host: {db_host}\n'
            f'    port: {int(db_port)}\n'
        )
    else:
        default_block = (
            '  default:\n'
            '    engine: postgresql\n'
            f'    name: {SCENARIO_DB_NAME}\n'
            '    user: postgres\n'
            '    password: admin\n'
            f'    host: {db_host}\n'
            f'    port: {int(db_port)}\n'
        )
    path.write_text(
        (
            'databases:\n'
            f'{default_block}'
            '  redis:\n'
            '    engine: redis\n'
            f'    host: {redis_host}\n'
            f'    port: {int(redis_port)}\n'
            '    user: ""\n'
            '    password: ""\n'
            '    db_channel: 0\n'
            '    db_cache: 1\n'
            '    db_celery_broker: 2\n'
            '    db_celery_result: 3\n'
        ),
        encoding='utf-8',
    )


def write_nginx_conf(
    path: Path,
    *,
    api_port: int,
    nginx_port: int,
    api_upstream: str = 'api',
    media_upstream: str = 'media-api',
    jupyter_upstream: str = '127.0.0.1',
    jupyter_port: int = 8002,
    media_port: int = 8003,
    module_runtime: str = 'monolith',
    microservice_modules: str = '',
    module_port: str = '',
) -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / 'docker'
        / 'nginx'
        / 'ergo_ms.docker.conf.template'
    )
    raw = {
        'DOCKER_SERVICE_API': api_upstream,
        'DOCKER_SERVICE_MEDIA': media_upstream,
        'API_PORT': str(api_port),
        'MEDIA_API_BIND_PORT': str(int(media_port)),
        'NGINX_LISTEN_PORT': str(nginx_port),
        'NGINX_SERVER_NAME': 'localhost',
        'MODULE_RUNTIME': module_runtime,
        'MICROSERVICE_MODULES': microservice_modules,
        'API_JUPYTER_BIND_PORT': str(int(jupyter_port)),
        'API_JUPYTER_ACCESS_MODE': 'nginx',
        'ERGO_JUPYTER': 'nginx',
    }
    if microservice_modules and module_port:
        key = microservice_modules.split(',')[0].strip().upper().replace('-', '_') + '_PORT'
        raw[key] = str(module_port)
    render_docker_nginx_config(raw, template_path=template, output_path=path)
    text = path.read_text(encoding='utf-8').replace('\r\n', '\n')
    text = text.replace('http://jupyter:', f'http://{jupyter_upstream}:')
    path.write_bytes(text.encode('utf-8'))


def write_modules_compose(path: Path, project_root: Path) -> int:
    names = modules_with_bridge(project_root)
    path.write_text(generate(names, {}), encoding='utf-8')
    return len(names)


def write_runtime_env(
    path: Path,
    *,
    ports: Mapping[str, int],
    api_secret: str,
    jwt_secret: str,
    media_internal_key: str,
    meili_key: str,
    jupyter_token: str,
    extra: Mapping[str, str] | None = None,
) -> None:
    """Одноразовый env прогона. Не корневой .env хоста."""
    media_port = int(ports.get('media', 8003))
    values = {
        'API_SECRET_KEY': api_secret,
        'API_JWT_SIGNING_KEY': jwt_secret,
        'MEDIA_API_INTERNAL_KEY': media_internal_key,
        'ERGO_ENV': 'development',
        'ERGO_RUNTIME': 'docker',
        'ERGO_BROKER': 'redis',
        'ERGO_DB': 'postgres',
        'ERGO_EMAIL': 'none',
        'ERGO_PROXY': 'nginx',
        'ERGO_MEDIA': 'local',
        'ERGO_JUPYTER': 'nginx',
        'ERGO_SEARCH_ENABLED': 'true',
        'DOCKER_ENABLED': 'true',
        'DOCKER_DATABASE': 'container',
        'DOCKER_PROFILE_POSTGRES': 'true',
        'REDIS_ENABLED': 'true',
        'NGINX_ENABLED': 'true',
        'MODULE_RUNTIME': 'monolith',
        'API_HOST': '0.0.0.0',
        'API_PORT': str(int(ports['api'])),
        'API_ALLOWED_HOSTS': 'localhost,127.0.0.1,api',
        'ERGO_DOCKER_DB_HOST': 'postgres',
        'ERGO_DOCKER_DB_PORT': '5432',
        'ERGO_DATABASES_YAML': '/app/databases.yaml',
        'REDIS_HOST': 'redis',
        'REDIS_PORT': '6379',
        'MEILI_HOST': 'http://meilisearch:7700',
        'MEILI_MASTER_KEY': meili_key,
        'MEDIA_API_BIND_HOST': '0.0.0.0',
        'MEDIA_API_BIND_PORT': str(media_port),
        'MEDIA_STORAGE_PATH': '/app/media',
        'ERGO_DOCKER_LOG_DIR': '/app/logs/docker',
        'ERGO_DOCKER_CONSOLE_OUTPUT': '1',
        'ERGO_DOCKER_REQUIRES_SETUP': '0',
        'ERGO_LOGS_DIR': '/app/logs',
        'TIME_ZONE': 'UTC',
        'API_JUPYTER_ACCESS_MODE': 'nginx',
        'API_JUPYTER_BIND_HOST': '0.0.0.0',
        'API_JUPYTER_BIND_PORT': str(int(ports['jupyter'])),
        'API_JUPYTER_TOKEN': jupyter_token,
    }
    if extra:
        values.update({key: str(value) for key, value in extra.items()})
    lines = [f'{key}={value}' for key, value in values.items()]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _volume_lines(items: list[tuple[str, str, str]]) -> str:
    lines = ['    volumes:']
    for source, dest, mode in items:
        suffix = f':{mode}' if mode else ''
        lines.append(f'      - "{source}:{dest}{suffix}"')
    return '\n'.join(lines)


def python_bind_specs(
    *,
    project_root: Path,
    run_dir: Path,
    extra: list[tuple[str, str, str]] | None = None,
    modules_dir: Path | None = None,
) -> list[tuple[str, str, str]]:
    modules = modules_dir if modules_dir is not None else project_root / 'modules'
    items: list[tuple[str, str, str]] = [
        (posix(project_root / 'core' / 'api'), '/app/core/api', 'ro'),
        (posix(project_root / 'core' / 'deployment'), '/app/core/deployment', 'ro'),
        (posix(project_root / 'core' / 'media_api'), '/app/core/media_api', 'ro'),
        (posix(project_root / 'core' / 'shared'), '/app/core/shared', 'ro'),
        (posix(modules), '/app/modules', 'ro'),
        (posix(run_dir / RUNTIME_ENV_NAME), '/app/.env', 'ro'),
        (posix(run_dir / 'databases.yaml'), '/app/databases.yaml', 'ro'),
        (posix(run_dir / 'logs'), '/app/logs', 'rw'),
        (posix(run_dir / 'media'), '/app/media', 'rw'),
    ]
    if extra:
        items.extend(extra)
    return items


def docker_volume_flags(specs: list[tuple[str, str, str]]) -> list[str]:
    flags: list[str] = []
    for source, dest, mode in specs:
        flags.extend(['-v', f'{source}:{dest}:{mode}'])
    return flags


def write_compose_file(
    path: Path,
    *,
    project_root: Path,
    run_dir: Path,
    ports: Mapping[str, int],
    meili_key: str,
    jupyter_token: str,
    project_name: str = COMPOSE_PROJECT,
) -> None:
    dist = posix(project_root / 'core' / 'client' / 'dist')
    logs = posix(run_dir / 'logs')
    nginx_conf = posix(run_dir / 'nginx.conf')
    static_api = posix(run_dir / 'static_api')
    api_port = int(ports['api'])
    nginx_port = int(ports['nginx'])
    jupyter_port = int(ports['jupyter'])
    pg_port = int(ports['postgres'])
    api_volumes = _volume_lines(python_bind_specs(project_root=project_root, run_dir=run_dir))
    jupyter_volumes = _volume_lines(
        python_bind_specs(
            project_root=project_root,
            run_dir=run_dir,
            extra=[
                (posix(run_dir / 'notebooks'), '/app/notebooks', 'rw'),
                (posix(run_dir / 'jupyter'), '/app/virtual_env/jupyter', 'rw'),
            ],
        )
    )

    text = f"""name: {project_name}
services:
  redis:
    image: {REDIS_IMAGE}
    networks: [scenario_net]
    restart: "no"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  postgres:
    image: {POSTGRES_IMAGE}
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: admin
      POSTGRES_DB: ergo_ms_scenario
    ports:
      - "127.0.0.1:{pg_port}:5432"
    networks: [scenario_net]
    restart: "no"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d ergo_ms_scenario"]
      interval: 5s
      timeout: 5s
      retries: 12

  meilisearch:
    image: {MEILI_IMAGE}
    environment:
      MEILI_ENV: development
      MEILI_MASTER_KEY: "{meili_key}"
      MEILI_NO_ANALYTICS: "true"
    networks: [scenario_net]
    restart: "no"
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1:7700/health"]
      interval: 5s
      timeout: 5s
      retries: 12

  api:
    image: {PYTHON_IMAGE}
    env_file:
      - {RUNTIME_ENV_NAME}
    environment:
      ERGO_DOCKER_SERVICE_NAME: api
      ERGO_DOCKER_REQUIRES_SETUP: "0"
      ERGO_DOCKER_CONSOLE_OUTPUT: "1"
      ERGO_PROCESS_ROLE: api
{api_volumes}
    ports:
      - "127.0.0.1:{api_port}:{api_port}"
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
      meilisearch:
        condition: service_healthy
    networks: [scenario_net]
    restart: "no"
    command: ["python", "core/api/scripts/start_api.py"]

  media-api:
    image: {PYTHON_IMAGE}
    env_file:
      - {RUNTIME_ENV_NAME}
    environment:
      ERGO_DOCKER_SERVICE_NAME: media-api
      ERGO_DOCKER_REQUIRES_SETUP: "0"
      ERGO_DOCKER_CONSOLE_OUTPUT: "1"
      ERGO_RUNTIME: docker
{api_volumes}
    expose:
      - "8003"
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    networks: [scenario_net]
    restart: "no"
    command: ["python", "core/api/scripts/start_media_api.py"]

  jupyter:
    image: {PYTHON_IMAGE}
    env_file:
      - {RUNTIME_ENV_NAME}
    environment:
      ERGO_DOCKER_SERVICE_NAME: jupyter
      ERGO_DOCKER_REQUIRES_SETUP: "0"
      ERGO_DOCKER_CONSOLE_OUTPUT: "1"
      ERGO_JUPYTER: nginx
      API_JUPYTER_ACCESS_MODE: nginx
      API_JUPYTER_BIND_HOST: "0.0.0.0"
      API_JUPYTER_BIND_PORT: "{jupyter_port}"
      API_JUPYTER_TOKEN: "{jupyter_token}"
{jupyter_volumes}
    ports:
      - "127.0.0.1:{jupyter_port}:{jupyter_port}"
    depends_on:
      api:
        condition: service_started
    networks: [scenario_net]
    restart: "no"
    command: ["python", "/app/core/deployment/scenario_test/jupyter_boot.py"]

  nginx:
    image: {NGINX_IMAGE}
    ports:
      - "127.0.0.1:{nginx_port}:{nginx_port}"
    volumes:
      - "{nginx_conf}:/etc/nginx/conf.d/default.conf:ro"
      - "{dist}:/usr/share/nginx/html:ro"
      - "{static_api}:/usr/share/nginx/static:ro"
      - "{logs}:/var/log/ergo:rw"
    depends_on:
      - api
      - media-api
    networks: [scenario_net]
    restart: "no"

networks:
  scenario_net:
    name: {project_name}_net
"""
    path.write_text(text, encoding='utf-8')
