"""
Генерация docker-compose.workers.generated.yml из celery_workers.yaml.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

DOCKER_DIR = Path(__file__).resolve().parent
DEPLOYMENT_DIR = DOCKER_DIR.parent
PROJECT_ROOT = DEPLOYMENT_DIR.parent.parent
WORKERS_CONFIG = PROJECT_ROOT / 'celery_workers.yaml'
WORKERS_EXAMPLE = PROJECT_ROOT / 'celery_workers.yaml.example'
OUTPUT = DOCKER_DIR / 'docker-compose.workers.generated.yml'

if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402

# Дублирует x-python-volumes из docker-compose.yml (якоря не работают между -f файлами).
PYTHON_VOLUMES_YAML = """    volumes:
      - ${ERGO_PROJECT_ROOT:-../../..}:/app
      - ./.compose.databases.yaml:/app/databases.yaml:ro
      - ${ERGO_LOGS_BIND:-../../../logs}:/app/logs
      - ${ERGO_MEDIA_BIND:-../../../media}:/app/media
      - ${ERGO_CELERY_CACHE_BIND:-celery_cache}:/app/virtual_env/cache
      - poetry_venv:/app/virtual_env/python"""

PYTHON_SERVICE_TEMPLATE = """
  celery-worker-{name}:
    image: ${{DOCKER_PYTHON_IMAGE:-ergo_ms-python:local}}
    env_file:
      - .compose.env
    environment:
      ERGO_DOCKER_SERVICE_NAME: celery-worker-{name}
      ERGO_DOCKER_REQUIRES_SETUP: "1"
{volumes}
    depends_on:
      redis:
        condition: service_started
{depends_on_api}
    networks:
      - ergo_net
    command: ["python", "core/api/scripts/start_celery_worker.py", "--worker={worker_key}"]
    restart: unless-stopped
"""


def load_workers_config() -> dict[str, Any]:
    path = WORKERS_CONFIG if WORKERS_CONFIG.is_file() else WORKERS_EXAMPLE
    if not path.is_file():
        return {}
    with open(path, encoding='utf-8') as handle:
        data = yaml.safe_load(handle) or {}
    return data.get('workers') or {}


_API_DEPENDS = """      api:
        condition: service_started
"""


def generate(workers: dict[str, Any], *, depends_on_api: bool = True) -> str:
    header = """# Автогенерация: ergoms docker-gen-workers (не редактировать вручную)
services:
"""
    if not workers:
        body = "  {}\n"
        return header + body

    api_block = _API_DEPENDS if depends_on_api else ''
    blocks = []
    for name in workers:
        safe = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in name)
        blocks.append(
            PYTHON_SERVICE_TEMPLATE.format(
                name=safe,
                worker_key=name,
                volumes=PYTHON_VOLUMES_YAML,
                depends_on_api=api_block,
            )
        )
    return header + '\n'.join(blocks)


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate docker-compose workers fragment')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument(
        '--quiet',
        action='store_true',
        help=t('help_quiet_success'),
    )
    args = parser.parse_args()

    from env_resolvers import load_merged_env
    from lifecycle.host_profile import SERVICE_API, SERVICE_YAML_WORKERS, resolve_host_profile

    environ = {**os.environ, **{k: str(v) for k, v in load_merged_env(PROJECT_ROOT).items()}}
    profile = resolve_host_profile(environ)
    workers = load_workers_config() if profile.wants(SERVICE_YAML_WORKERS) else {}
    content = generate(workers, depends_on_api=profile.wants(SERVICE_API))
    args.output.write_text(content, encoding='utf-8')
    if not args.quiet:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        print(t('generated_worker_services', count=len(workers), output=args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
