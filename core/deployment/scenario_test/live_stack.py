"""Запуск изолированного стека через docker run, без compose up.

На Docker Desktop Windows compose up, пользовательская сеть и -p зависают на Starting.
docker run -d на default bridge без публикации портов проходит.
HTTP проверяем через docker exec внутри контейнеров.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from scenario_test.stack import (
    MEILI_IMAGE,
    MYSQL_IMAGE,
    MSSQL_IMAGE,
    NGINX_IMAGE,
    POSTGRES_IMAGE,
    PYTHON_IMAGE,
    REDIS_IMAGE,
    RUNTIME_ENV_NAME,
    SCENARIO_DB_NAME,
    SCENARIO_MSSQL_PASSWORD,
    SCENARIO_MYSQL_PASSWORD,
    docker_volume_flags,
    posix,
    python_bind_specs,
)


def container_names(project: str) -> dict[str, str]:
    return {
        'redis': f'{project}_redis',
        'postgres': f'{project}_postgres',
        'mysql': f'{project}_mysql',
        'mssql': f'{project}_mssql',
        'meilisearch': f'{project}_meilisearch',
        'api': f'{project}_api',
        'media': f'{project}_media',
        'jupyter': f'{project}_jupyter',
        'nginx': f'{project}_nginx',
        'module': f'{project}_module',
    }


def all_container_names(project: str) -> list[str]:
    return list(container_names(project).values())


def container_running(name: str) -> bool:
    result = subprocess.run(
        ['docker', 'inspect', '-f', '{{.State.Running}}', name],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return (result.stdout or '').strip().lower() == 'true'


def container_ip(name: str) -> str:
    templates = (
        '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}',
        '{{.NetworkSettings.IPAddress}}',
    )
    for fmt in templates:
        result = subprocess.run(
            ['docker', 'inspect', '-f', fmt, name],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            continue
        for token in (result.stdout or '').split():
            ip = token.strip()
            if ip:
                return ip
    return ''


def add_host_flags(mapping: Mapping[str, str]) -> list[str]:
    flags: list[str] = []
    for host, ip in mapping.items():
        if ip:
            flags.extend(['--add-host', f'{host}:{ip}'])
    return flags


def infra_run_commands(
    *,
    project: str,
    ports: Mapping[str, int],
    meili_key: str,
    db: str = 'postgres',
    use_redis: bool = True,
) -> list[list[str]]:
    names = container_names(project)
    commands: list[list[str]] = []
    if use_redis:
        commands.append(
            [
                'docker',
                'run',
                '-d',
                '--name',
                names['redis'],
                REDIS_IMAGE,
            ]
        )
    if db == 'postgres':
        commands.append(
            [
                'docker',
                'run',
                '-d',
                '--name',
                names['postgres'],
                '-e',
                'POSTGRES_USER=postgres',
                '-e',
                'POSTGRES_PASSWORD=admin',
                '-e',
                f'POSTGRES_DB={SCENARIO_DB_NAME}',
                POSTGRES_IMAGE,
            ]
        )
    elif db == 'mysql':
        commands.append(
            [
                'docker',
                'run',
                '-d',
                '--name',
                names['mysql'],
                '-e',
                f'MYSQL_ROOT_PASSWORD={SCENARIO_MYSQL_PASSWORD}',
                '-e',
                f'MYSQL_DATABASE={SCENARIO_DB_NAME}',
                MYSQL_IMAGE,
            ]
        )
    elif db == 'mssql':
        commands.append(
            [
                'docker',
                'run',
                '-d',
                '--name',
                names['mssql'],
                '-e',
                'ACCEPT_EULA=Y',
                '-e',
                f'MSSQL_SA_PASSWORD={SCENARIO_MSSQL_PASSWORD}',
                MSSQL_IMAGE,
            ]
        )
    commands.append(
        [
            'docker',
            'run',
            '-d',
            '--name',
            names['meilisearch'],
            '-e',
            'MEILI_ENV=development',
            '-e',
            f'MEILI_MASTER_KEY={meili_key}',
            '-e',
            'MEILI_NO_ANALYTICS=true',
            MEILI_IMAGE,
        ]
    )
    return commands


def python_run_command(
    *,
    project: str,
    service: str,
    run_dir: Path,
    project_root: Path,
    extra_env: Sequence[tuple[str, str]] = (),
    extra_volumes: list[tuple[str, str, str]] | None = None,
    extra_hosts: Mapping[str, str] | None = None,
    command: Sequence[str],
    modules_dir: Path | None = None,
) -> list[str]:
    names = container_names(project)
    env_file = posix(run_dir / RUNTIME_ENV_NAME)
    cmd = [
        'docker',
        'run',
        '-d',
        '--name',
        names[service],
        '-w',
        '/app',
        '--env-file',
        env_file,
        '-e',
        'ERGO_DOCKER_REQUIRES_SETUP=0',
        '-e',
        'ERGO_DOCKER_CONSOLE_OUTPUT=1',
    ]
    cmd.extend(add_host_flags(extra_hosts or {}))
    for key, value in extra_env:
        cmd.extend(['-e', f'{key}={value}'])
    cmd.extend(docker_volume_flags(
        python_bind_specs(
            project_root=project_root,
            run_dir=run_dir,
            extra=extra_volumes,
            modules_dir=modules_dir if modules_dir is not None else run_dir / 'modules',
        )
    ))
    cmd.append(PYTHON_IMAGE)
    cmd.extend(list(command))
    return cmd


def app_run_commands(
    *,
    project: str,
    project_root: Path,
    run_dir: Path,
    jupyter_token: str,
    extra_hosts: Mapping[str, str],
    api_host: str,
    media_host: str,
    jupyter_host: str = '127.0.0.1',
    include_jupyter: bool = True,
    include_nginx: bool = True,
    jupyter_mode: str = 'nginx',
) -> list[list[str]]:
    names = container_names(project)
    dist = posix(project_root / 'core' / 'client' / 'dist')
    logs = posix(run_dir / 'logs')
    nginx_conf = posix(run_dir / 'nginx.conf')
    static_api = posix(run_dir / 'static_api')
    jupyter_extra = [
        (posix(run_dir / 'notebooks'), '/app/notebooks', 'rw'),
        (posix(run_dir / 'jupyter'), '/app/virtual_env/jupyter', 'rw'),
    ]
    nginx_hosts = dict(extra_hosts)
    nginx_hosts['api'] = api_host
    nginx_hosts['media-api'] = media_host
    nginx_hosts['jupyter'] = jupyter_host
    commands = [
        python_run_command(
            project=project,
            service='api',
            run_dir=run_dir,
            project_root=project_root,
            extra_hosts=extra_hosts,
            extra_env=(('ERGO_DOCKER_SERVICE_NAME', 'api'), ('ERGO_PROCESS_ROLE', 'api')),
            command=('python', 'core/api/scripts/start_api.py'),
        ),
        python_run_command(
            project=project,
            service='media',
            run_dir=run_dir,
            project_root=project_root,
            extra_hosts=extra_hosts,
            extra_env=(('ERGO_DOCKER_SERVICE_NAME', 'media-api'),),
            command=('python', 'core/api/scripts/start_media_api.py'),
        ),
    ]
    if include_jupyter:
        commands.append(
            python_run_command(
                project=project,
                service='jupyter',
                run_dir=run_dir,
                project_root=project_root,
                extra_hosts=extra_hosts,
                extra_env=(
                    ('ERGO_DOCKER_SERVICE_NAME', 'jupyter'),
                    ('ERGO_JUPYTER', jupyter_mode),
                    ('API_JUPYTER_ACCESS_MODE', jupyter_mode),
                    ('API_JUPYTER_BIND_HOST', '0.0.0.0'),
                    ('API_JUPYTER_TOKEN', jupyter_token),
                ),
                extra_volumes=jupyter_extra,
                command=('python', '/app/core/deployment/scenario_test/jupyter_boot.py'),
            )
        )
    if include_nginx:
        commands.append(
            [
                'docker',
                'run',
                '-d',
                '--name',
                names['nginx'],
                *add_host_flags(nginx_hosts),
                '-v',
                f'{nginx_conf}:/etc/nginx/conf.d/default.conf:ro',
                '-v',
                f'{dist}:/usr/share/nginx/html:ro',
                '-v',
                f'{static_api}:/usr/share/nginx/static:ro',
                '-v',
                f'{logs}:/var/log/ergo',
                NGINX_IMAGE,
            ]
        )
    return commands


def module_run_command(
    *,
    project: str,
    project_root: Path,
    run_dir: Path,
    extra_hosts: Mapping[str, str],
    module_name: str,
    module_port: str,
) -> list[str]:
    return python_run_command(
        project=project,
        service='module',
        run_dir=run_dir,
        project_root=project_root,
        extra_hosts=extra_hosts,
        extra_env=(
            ('ERGO_DOCKER_SERVICE_NAME', module_name),
            ('ERGO_PROCESS_ROLE', f'module:{module_name}'),
            ('PROCESS_MODULES', module_name),
            ('MODULE_RUNTIME', 'microservice'),
            ('MODULE_API_BIND_PORT', str(module_port)),
            ('API_PORT', str(module_port)),
        ),
        modules_dir=project_root / 'modules',
        command=('python', 'core/api/scripts/start_module_api.py', f'--module={module_name}'),
    )


def migrate_command(
    *,
    project: str,
    project_root: Path,
    run_dir: Path,
    extra_hosts: Mapping[str, str],
) -> list[str]:
    env_file = posix(run_dir / RUNTIME_ENV_NAME)
    cmd = [
        'docker',
        'run',
        '--rm',
        '--name',
        f'{project}_migrate',
        *add_host_flags(extra_hosts),
        '--env-file',
        env_file,
        '-e',
        'ERGO_DOCKER_REQUIRES_SETUP=0',
        '-w',
        '/app/core/api',
    ]
    cmd.extend(docker_volume_flags(
        python_bind_specs(
            project_root=project_root,
            run_dir=run_dir,
            modules_dir=run_dir / 'modules',
        )
    ))
    cmd.extend(
        [
            PYTHON_IMAGE,
            'python',
            '-m',
            'commands',
            'migrate',
        ]
    )
    return cmd


def exec_command(
    container: str,
    *args: str,
    workdir: str | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> list[str]:
    cmd = ['docker', 'exec']
    for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
        cmd.extend(['-e', f'{key}='])
    cmd.extend(['-e', 'NO_PROXY=*', '-e', 'no_proxy=*'])
    for key, value in (extra_env or {}).items():
        cmd.extend(['-e', f'{key}={value}'])
    if workdir:
        cmd.extend(['-w', workdir])
    cmd.extend([container, *args])
    return cmd
