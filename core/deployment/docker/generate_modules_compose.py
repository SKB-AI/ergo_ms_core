"""
Генерация docker-compose.modules.generated.yml из MICROSERVICE_MODULES.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DOCKER_DIR = Path(__file__).resolve().parent
DEPLOYMENT_DIR = DOCKER_DIR.parent
PROJECT_ROOT = DEPLOYMENT_DIR.parent.parent
OUTPUT = DOCKER_DIR / 'docker-compose.modules.generated.yml'

if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402

PYTHON_VOLUMES_YAML = """    volumes:
      - ${ERGO_PROJECT_ROOT:-../../..}:/app
      - ./.compose.databases.yaml:/app/databases.yaml:ro
      - ${ERGO_LOGS_BIND:-../../../logs}:/app/logs
      - ${ERGO_MEDIA_BIND:-../../../media}:/app/media
      - ${ERGO_CELERY_CACHE_BIND:-celery_cache}:/app/virtual_env/cache
      - poetry_venv:/app/virtual_env/python"""

SERVICE_TEMPLATE = """
  {name}:
    image: ${{DOCKER_PYTHON_IMAGE:-ergo_ms-python:local}}
    env_file:
      - .compose.env
    environment:
      ERGO_DOCKER_SERVICE_NAME: "{name}"
      ERGO_DOCKER_REQUIRES_SETUP: "1"
      ERGO_PROCESS_ROLE: module:{name}
      PROCESS_MODULES: "{name}"
      MODULE_API_BIND_PORT: "{port}"
{volumes}
    expose:
      - "{port}"
    depends_on:
      redis:
        condition: service_healthy
{depends_on_api}
    networks:
      - ergo_net
    command: ["python", "core/api/scripts/start_module_api.py", "--module={name}"]
    restart: unless-stopped
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/api/system/ready/')",
        ]
      interval: 15s
      timeout: 5s
      retries: 5

  {name}-worker:
    image: ${{DOCKER_PYTHON_IMAGE:-ergo_ms-python:local}}
    env_file:
      - .compose.env
    environment:
      ERGO_DOCKER_SERVICE_NAME: "{name}-worker"
      ERGO_DOCKER_REQUIRES_SETUP: "1"
      ERGO_PROCESS_ROLE: module:{name}
      PROCESS_MODULES: "{name}"
{volumes}
    depends_on:
      redis:
        condition: service_healthy
      {name}:
        condition: service_started
    networks:
      - ergo_net
    command: ["python", "core/api/scripts/start_celery_worker.py", "--module={name}"]
    restart: unless-stopped
"""

BEAT_TEMPLATE = """
  {name}-beat:
    image: ${{DOCKER_PYTHON_IMAGE:-ergo_ms-python:local}}
    env_file:
      - .compose.env
    environment:
      ERGO_DOCKER_SERVICE_NAME: "{name}-beat"
      ERGO_DOCKER_REQUIRES_SETUP: "1"
      ERGO_PROCESS_ROLE: module:{name}
      PROCESS_MODULES: "{name}"
{volumes}
    depends_on:
      redis:
        condition: service_healthy
      {name}:
        condition: service_started
    networks:
      - ergo_net
    command: ["python", "core/api/scripts/start_celery_beat.py", "--module={name}"]
    restart: unless-stopped
"""


def parse_modules(raw: str = '') -> list[str]:
    return [m.strip() for m in (raw or '').split(',') if m.strip()]


def _is_microservice(runtime: str) -> bool:
    return runtime.strip().lower() in ('microservice', 'split')


def _modules_from_env(environ: dict[str, str]) -> list[str]:
    raw = environ.get('MICROSERVICE_MODULES', '')
    return parse_modules(raw)


def module_port(name: str, environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    key = name.upper().replace('-', '_')
    explicit = (env.get(f'{key}_PORT') or '').strip()
    if explicit:
        return explicit
    return str(8100 + (sum(ord(c) for c in name) % 500))


_API_DEPENDS = """      api:
        condition: service_started
"""


def module_has_beat_schedule(name: str, project_root: Path | None = None) -> bool:
    """Есть ли у модуля celery_beat_config.py — тогда нужен свой Beat."""
    root = project_root or PROJECT_ROOT
    api_dir = root / 'modules' / name / 'api'
    if not api_dir.is_dir():
        return False
    return any(api_dir.rglob('celery_beat_config.py'))


def generate(
    modules: list[str],
    environ: dict[str, str] | None = None,
    *,
    depends_on_api: bool = True,
    beat_modules: frozenset[str] | None = None,
) -> str:
    header = """# Автогенерация: ergoms docker-gen-modules (не редактировать вручную)
services:
"""
    if not modules:
        return header + "  {}\n"

    api_block = _API_DEPENDS if depends_on_api else ''
    scheduled = (
        beat_modules
        if beat_modules is not None
        else frozenset(name for name in modules if module_has_beat_schedule(name))
    )
    blocks = []
    for name in modules:
        port = module_port(name, environ)
        blocks.append(
            SERVICE_TEMPLATE.format(
                name=name,
                port=port,
                volumes=PYTHON_VOLUMES_YAML,
                depends_on_api=api_block,
            )
        )
        if name in scheduled:
            blocks.append(
                BEAT_TEMPLATE.format(
                    name=name,
                    volumes=PYTHON_VOLUMES_YAML,
                )
            )
    return header + '\n'.join(blocks)


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate docker-compose modules fragment')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument(
        '--quiet',
        action='store_true',
        help=t('help_quiet_success'),
    )
    args = parser.parse_args()

    environ = dict(os.environ)
    from env_resolvers import load_merged_env

    merged = load_merged_env(PROJECT_ROOT)
    environ = {**environ, **{k: str(v) for k, v in merged.items()}}
    runtime = (environ.get('MODULE_RUNTIME') or '').strip().lower()
    modules = _modules_from_env(environ) if _is_microservice(runtime) else []

    if not runtime:
        compose_env = DOCKER_DIR / '.compose.env'
        if compose_env.is_file():
            from env_file_loader import parse_env_file

            values = parse_env_file(compose_env)
            environ = {**environ, **values}
            runtime = (environ.get('MODULE_RUNTIME') or 'monolith').strip().lower()
            if _is_microservice(runtime):
                modules = _modules_from_env(environ)

    if not runtime:
        runtime = 'monolith'

    if not _is_microservice(runtime):
        modules = []

    from lifecycle.host_profile import SERVICE_API, resolve_host_profile

    content = generate(
        modules,
        environ,
        depends_on_api=resolve_host_profile(environ).wants(SERVICE_API),
    )
    args.output.write_text(content, encoding='utf-8')
    if not args.quiet:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        print(t('generated_module_services', count=len(modules), output=args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
