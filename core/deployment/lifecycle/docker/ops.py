"""Docker Compose операции (вынесено из docker_cli для lifecycle и CLI)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DOCKER_DIR = Path(__file__).resolve().parent.parent.parent / 'docker'
DEPLOYMENT_DIR = DOCKER_DIR.parent
PROJECT_ROOT = DEPLOYMENT_DIR.parent.parent

if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))
if str(DOCKER_DIR) not in sys.path:
    sys.path.insert(0, str(DOCKER_DIR))
_NGINX_DIR = DEPLOYMENT_DIR / 'nginx'
if str(_NGINX_DIR) not in sys.path:
    sys.path.insert(0, str(_NGINX_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from env_resolvers import load_merged_env  # noqa: E402
from ergo_modes import effective_docker_enabled, effective_docker_profile_meilisearch, effective_nginx_enabled, env_bool_key  # noqa: E402
from docker_runtime import (  # noqa: E402
    BUILD_CACHE_OUTPUT,
    compose_profiles,
    docker_mode,
    effective_docker_build_policy,
    load_redis_password,
    postgres_container_env,
    prepare_compose_artifacts,
)

# render_common → security → PyYAML; не импортировать на уровне модуля —
# setup-full / scaffold идут на portable Python до python-install.
SETUP_MARKER_REL = Path('logs/.ergo-docker-setup-ok')
DOCKER_PYTHON_INSTALL_LOG = 'logs/docker/python-install.log'
DOCKER_NPM_INSTALL_LOG = 'logs/docker/npm-install.log'

from lifecycle.docker.ignore import DOCKERIGNORE_ARTIFACT_PATHS  # noqa: E402

def compose_docker_dir(project_root: Path | None = None) -> Path:
    if project_root is not None:
        candidate = Path(project_root).resolve() / 'core' / 'deployment' / 'docker'
        if candidate.is_dir():
            return candidate
    return DOCKER_DIR.resolve()


COMPOSE_ARTIFACT_PATHS = (
    DOCKER_DIR / '.compose.env',
    DOCKER_DIR / '.compose.databases.yaml',
    DOCKER_DIR / '.compose.databases.loadtest.yaml',
    DOCKER_DIR / 'docker-compose.workers.generated.yml',
    DOCKER_DIR / 'docker-compose.modules.generated.yml',
    DOCKER_DIR / 'docker-compose.build.generated.yml',
    DOCKER_DIR / 'docker-compose.publish.generated.yml',
    DOCKER_DIR / 'docker-compose.redis-auth.generated.yml',
    DOCKER_DIR / 'init' / 'postgres' / '02-celery-databases.sql',
    DOCKER_DIR / 'nginx' / 'ergo_ms.conf.rendered',
    *DOCKERIGNORE_ARTIFACT_PATHS,
)


def find_docker_compose() -> list[str]:
    if shutil.which('docker'):
        result = subprocess.run(
            ['docker', 'compose', 'version'],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return ['docker', 'compose']
    return []


def compose_file_list(
    mode: str,
    raw_env: dict[str, str],
    *,
    docker_home: Path | None = None,
) -> list[Path]:
    home = docker_home or DOCKER_DIR
    files = [
        home / 'docker-compose.yml',
        home / f'docker-compose.{mode}.yml',
    ]
    build_cache = home / BUILD_CACHE_OUTPUT.name
    if build_cache.is_file():
        files.append(build_cache)
    if _env_db_container(raw_env):
        files.append(home / 'docker-compose.postgres.yml')
    if env_bool_key(raw_env, 'DOCKER_PROFILE_NGINX'):
        files.append(home / 'docker-compose.nginx.yml')
    if env_bool_key(raw_env, 'DOCKER_PROFILE_JUPYTER'):
        files.append(home / 'docker-compose.jupyter.yml')
    if env_bool_key(raw_env, 'DOCKER_PROFILE_LOADTEST'):
        files.append(home / 'docker-compose.loadtest.yml')
    if env_bool_key(raw_env, 'DOCKER_PROFILE_MEILISEARCH') or effective_docker_profile_meilisearch(raw_env):
        files.append(home / 'docker-compose.meilisearch.yml')
    workers = home / 'docker-compose.workers.generated.yml'
    from lifecycle.host_profile import SERVICE_YAML_WORKERS, resolve_host_profile

    if workers.is_file() and resolve_host_profile(raw_env).wants(SERVICE_YAML_WORKERS):
        files.append(workers)
    modules = home / 'docker-compose.modules.generated.yml'
    runtime = (raw_env.get('MODULE_RUNTIME') or 'monolith').strip().lower()
    if runtime in ('microservice', 'split') and modules.is_file():
        files.append(modules)
    publish = home / 'docker-compose.publish.generated.yml'
    if publish.is_file():
        files.append(publish)
    redis_auth = home / 'docker-compose.redis-auth.generated.yml'
    if redis_auth.is_file():
        files.append(redis_auth)
    return files


def compose_file_list_full(*, docker_home: Path | None = None) -> list[Path]:
    home = docker_home or DOCKER_DIR
    files = [
        home / 'docker-compose.yml',
        home / 'docker-compose.dev.yml',
        home / 'docker-compose.prod.yml',
        home / 'docker-compose.postgres.yml',
        home / 'docker-compose.nginx.yml',
        home / 'docker-compose.jupyter.yml',
        home / 'docker-compose.loadtest.yml',
        home / 'docker-compose.meilisearch.yml',
    ]
    build_cache = home / BUILD_CACHE_OUTPUT.name
    if build_cache.is_file():
        files.append(build_cache)
    workers = home / 'docker-compose.workers.generated.yml'
    if workers.is_file():
        files.append(workers)
    modules = home / 'docker-compose.modules.generated.yml'
    if modules.is_file():
        files.append(modules)
    publish = home / 'docker-compose.publish.generated.yml'
    if publish.is_file():
        files.append(publish)
    redis_auth = home / 'docker-compose.redis-auth.generated.yml'
    if redis_auth.is_file():
        files.append(redis_auth)
    return files


def _docker_image_exists(image_ref: str) -> bool:
    if not shutil.which('docker'):
        return False
    result = subprocess.run(
        ['docker', 'image', 'inspect', image_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _required_local_images(raw_env: dict[str, str]) -> list[str]:
    images = [
        raw_env.get('DOCKER_PYTHON_IMAGE', 'ergo_ms-python:local').strip() or 'ergo_ms-python:local',
        raw_env.get('DOCKER_NODE_IMAGE', 'ergo_ms-client:local').strip() or 'ergo_ms-client:local',
    ]
    return images


def should_skip_build(raw_env: dict[str, str]) -> bool:
    if effective_docker_build_policy(raw_env) != 'if-missing':
        return False
    return all(_docker_image_exists(name) for name in _required_local_images(raw_env))


def build_subprocess_env(raw_env: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env['DOCKER_BUILDKIT'] = '1'
    env['COMPOSE_DOCKER_CLI_BUILD'] = '1'
    return env


def _env_db_container(raw_env: dict[str, str]) -> bool:
    mode = raw_env.get('DOCKER_DATABASE', 'container').strip().lower()
    if mode != 'container':
        return False
    return env_bool_key(raw_env, 'DOCKER_PROFILE_POSTGRES', default=True)


def bootstrap_service_names(raw_env: dict[str, str]) -> list[str]:
    services = ['redis']
    if _env_db_container(raw_env):
        services.append('postgres')
    return services


def render_nginx_docker_config(raw_env: dict[str, str]) -> Path:
    from render_common import render_docker_nginx_config  # noqa: WPS433

    template = DOCKER_DIR / 'nginx' / 'ergo_ms.docker.conf.template'
    output = DOCKER_DIR / 'nginx' / 'ergo_ms.conf.rendered'
    return render_docker_nginx_config(
        raw_env,
        template_path=template,
        output_path=output,
    )


def warn_conflicts(raw_env: dict[str, str]) -> None:
    from ergo_modes import effective_docker_enabled, effective_nginx_enabled, effective_redis_enabled

    if not effective_docker_enabled(raw_env) and not env_bool_key(raw_env, 'DOCKER_ENABLED'):
        return
    if effective_redis_enabled(raw_env) and raw_env.get('REDIS_HOST', '127.0.0.1') not in (
        'redis',
        '127.0.0.1',
        'localhost',
        '',
    ):
        print(format_console(
            'warning',
            t('docker_redis_host_remap_warn'),
        ))
    if effective_nginx_enabled(raw_env) and not env_bool_key(raw_env, 'DOCKER_PROFILE_NGINX'):
        print(
            format_console(
                'warning',
                t('docker_nginx_profile_conflict'),
            )
        )


def run_generate_workers(*, quiet: bool = False, project_root: Path | None = None) -> int:
    home = compose_docker_dir(project_root)
    script = home / 'generate_workers_compose.py'
    cmd = [sys.executable, str(script)]
    if quiet:
        cmd.append('--quiet')
    return subprocess.call(cmd, cwd=str(home))


def run_generate_modules(
    *,
    quiet: bool = False,
    env: dict[str, str] | None = None,
    project_root: Path | None = None,
) -> int:
    home = compose_docker_dir(project_root)
    script = home / 'generate_modules_compose.py'
    cmd = [sys.executable, str(script)]
    if quiet:
        cmd.append('--quiet')
    return subprocess.call(cmd, cwd=str(home), env=env)


def build_compose_cmd(
    action: str,
    *,
    mode: str | None = None,
    extra_args: list[str] | None = None,
    for_clean: bool = False,
    project_root: Path | None = None,
) -> tuple[list[str], Path]:
    compose_bin = find_docker_compose()
    if not compose_bin:
        print(format_console('error', t('docker_not_found_install')), file=sys.stderr)
        sys.exit(1)

    root = (project_root or PROJECT_ROOT).resolve()
    home = compose_docker_dir(root)
    raw = dict(load_merged_env(root))
    # Процессное окружение перекрывает .env для профилей / loadtest портов.
    for key, value in os.environ.items():
        if not value or not value.strip():
            continue
        if key.startswith('DOCKER_PROFILE_') or key.startswith('LOADTEST_'):
            raw[key] = value
    if mode:
        raw['DOCKER_MODE'] = mode

    prepare_compose_artifacts(
        root,
        resolve_app_ports=(action == 'up'),
        warn_image_bases=(action == 'build'),
    )
    workers_file = home / 'docker-compose.workers.generated.yml'
    if for_clean:
        if not workers_file.is_file():
            print(format_console('info', t('preparing_worker_services_list')))
            run_generate_workers(quiet=True, project_root=root)
    elif not workers_file.is_file():
        run_generate_workers(project_root=root)

    modules_env = {**os.environ, **{k: str(v) for k, v in raw.items()}}
    run_generate_modules(quiet=True, env=modules_env, project_root=root)

    if for_clean or env_bool_key(raw, 'DOCKER_PROFILE_NGINX'):
        render_nginx_docker_config(raw)

    if not for_clean:
        warn_conflicts(raw)

    cmd = [*compose_bin]
    compose_files = (
        compose_file_list_full(docker_home=home)
        if for_clean
        else compose_file_list(docker_mode(raw), raw, docker_home=home)
    )
    for compose_file in compose_files:
        cmd.extend(['-f', str(compose_file)])

    profiles = (
        [
            'postgres',
            'nginx',
            'jupyter',
            'loadtest',
            'meilisearch',
            'host-api',
            'host-media',
            'host-beat',
        ]
        if for_clean
        else compose_profiles(raw)
    )
    if action in ('run', 'exec') and extra_args and 'api' in extra_args:
        if 'host-api' not in profiles:
            profiles.append('host-api')
    for profile in profiles:
        cmd.extend(['--profile', profile])

    cmd.extend(['--env-file', str(home / '.compose.env')])
    cmd.append(action)
    if extra_args:
        cmd.extend(extra_args)
    return cmd, home


def run_api_oneoff(
    shell: str,
    *,
    mode: str | None = None,
    skip_infra_wait: bool = False,
    project_root: Path | None = None,
) -> int:
    extra_args = [
        '--rm',
        '--no-deps',
        '-T',
        '-e',
        'ERGO_DOCKER_SERVICE_NAME=',
        '-e',
        'ERGO_DOCKER_CONSOLE_OUTPUT=1',
    ]
    if skip_infra_wait:
        extra_args.extend(['-e', 'ERGO_DOCKER_SKIP_INFRA_WAIT=1'])
    extra_args.extend(
        [
            'api',
            'bash',
            '-o',
            'pipefail',
            '-c',
            shell,
        ],
    )
    cmd, cwd = build_compose_cmd(
        'run',
        mode=mode,
        extra_args=extra_args,
        project_root=project_root,
    )
    return subprocess.call(cmd, cwd=str(cwd))


def wait_bootstrap_infra(
    mode: str | None,
    raw_env: dict[str, str],
    timeout_sec: float = 180.0,
    *,
    project_root: Path | None = None,
) -> bool:
    services = bootstrap_service_names(raw_env)
    pg_user = postgres_container_env(raw_env).get('POSTGRES_USER', 'postgres')
    deadline = time.monotonic() + timeout_sec
    redis_password = load_redis_password(project_root or PROJECT_ROOT)
    redis_ping_args = ['-T', 'redis', 'redis-cli']
    if redis_password:
        redis_ping_args.extend(['-a', redis_password, '--no-auth-warning'])
    redis_ping_args.append('ping')

    while time.monotonic() < deadline:
        ping_cmd, ping_cwd = build_compose_cmd(
            'exec',
            mode=mode,
            extra_args=redis_ping_args,
            project_root=project_root,
        )
        ping = subprocess.run(ping_cmd, cwd=str(ping_cwd), capture_output=True, text=True, check=False)
        redis_ok = ping.returncode == 0 and 'PONG' in (ping.stdout or '')

        postgres_ok = True
        if 'postgres' in services:
            pg_cmd, pg_cwd = build_compose_cmd(
                'exec',
                mode=mode,
                extra_args=['-T', 'postgres', 'pg_isready', '-U', pg_user],
                project_root=project_root,
            )
            pg = subprocess.run(pg_cmd, cwd=str(pg_cwd), capture_output=True, text=True, check=False)
            postgres_ok = pg.returncode == 0

        if redis_ok and postgres_ok:
            return True
        time.sleep(2)

    print(format_console('error', t('redis_postgres_not_ready_check')), file=sys.stderr)
    return False


def setup_marker_path(project_root: Path) -> Path:
    return project_root / SETUP_MARKER_REL


def setup_marker_exists(project_root: Path) -> bool:
    return setup_marker_path(project_root).is_file()


def clear_setup_marker(project_root: Path) -> None:
    marker = setup_marker_path(project_root)
    if marker.is_file():
        marker.unlink()


def mark_setup_complete(project_root: Path) -> None:
    marker = setup_marker_path(project_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    (marker.parent / 'docker').mkdir(parents=True, exist_ok=True)
    marker.touch()
    print(format_console('ok', t('setup_complete_marker', path=SETUP_MARKER_REL.as_posix())))


def npm_client_service(mode: str) -> str:
    return 'client' if mode == 'dev' else 'client-build'


def _api_pythonpath_prefix() -> str:
    # poetry run меняет cwd на корень pyproject (/app); пакет commands — в core/api.
    return 'PYTHONPATH=/app/core/api:/app'


def api_install_shell() -> str:
    # entrypoint делает poetry env info и подменяет PATH на пустой volume venv.
    # Берём python образа, не poetry run.
    return (
        'mkdir -p /app/logs/docker '
        f'&& cd /app/core/api '
        f'&& {_api_pythonpath_prefix()} /usr/local/bin/python -m commands install '
        '2>&1 | tee -a /app/logs/docker/python-install.log'
    )


def api_migrate_shell() -> str:
    return (
        'mkdir -p /app/logs/docker '
        f'&& cd /app/core/api '
        f'&& {_api_pythonpath_prefix()} poetry run python -m commands migrate '
        '2>&1 | tee -a /app/logs/docker/migrate.log'
    )


def api_warmup_shell() -> str:
    return (
        'mkdir -p /app/logs/docker '
        f'&& cd /app/core/api '
        f'&& {_api_pythonpath_prefix()} poetry run python -m commands warmup_caches '
        '2>&1 | tee -a /app/logs/docker/warmup.log'
    )


def remove_compose_artifacts(project_root: Path | None = None) -> None:
    root = project_root or PROJECT_ROOT
    for path in COMPOSE_ARTIFACT_PATHS:
        if path.is_file():
            path.unlink()
            print(format_console('ok', t('removed_path', path=path.relative_to(root))))
