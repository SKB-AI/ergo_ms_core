"""
Isolated deployment scenario runner.

Does not write host .env, docker/.compose.env, or OS services.
Logs: virtual_env/cache/tmp/scenario-test/<stamp>/.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Sequence

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
_DOCKER_DIR = _DEPLOYMENT_DIR / 'docker'
_NGINX_DIR = _DEPLOYMENT_DIR / 'nginx'
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

for _path in (_DEPLOYMENT_DIR, _DOCKER_DIR, _NGINX_DIR):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from project_layout import cache_dir, ensure_dir  # noqa: E402
from scenario_test.docker_live import SKIP as DOCKER_SKIP  # noqa: E402
from scenario_test.docker_live import run_docker_scenario  # noqa: E402
from scenario_test.host_live import run_host_scenario  # noqa: E402
from scenario_test.isolation import sanitized_os_env, workspace_config_fingerprint  # noqa: E402
from scenario_test.matrix import filter_specs  # noqa: E402
from scenario_test.ports import pick_scenario_ports  # noqa: E402
from scenario_test.stack import (  # noqa: E402
    COMPOSE_PROJECT,
    PYTHON_IMAGE,
    REDIS_IMAGE,
    RUNTIME_ENV_NAME,
    write_compose_file,
    write_modules_compose,
    write_nginx_conf,
    write_runtime_env,
)


class RunLog:
    def __init__(self, path: Path, compose_log: Path) -> None:
        self.path = path
        self.compose_log = compose_log
        self._fh: IO[str] = path.open('a', encoding='utf-8')

    def write(self, line: str) -> None:
        stamp = datetime.now(timezone.utc).strftime('%H:%M:%S')
        text = f'{stamp} {line}'
        self._fh.write(text + '\n')
        self._fh.flush()
        print(text, flush=True)

    def close(self) -> None:
        self._fh.close()


class ComposeConfigFailed(RuntimeError):
    pass


_SECRET_LINE = re.compile(
    r'(?im)^(.*(?:API_SECRET_KEY|API_JWT_SIGNING_KEY|MEDIA_API_INTERNAL_KEY|'
    r'MEILI_MASTER_KEY|API_JUPYTER_TOKEN).*)(=|: ).+$'
)


def _redact(text: str) -> str:
    return _SECRET_LINE.sub(r'\1\2***', text)


def _kill_process_tree(pid: int) -> None:
    if sys.platform == 'win32':
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        return
    try:
        os.killpg(pid, 9)
    except OSError:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _docker() -> list[str] | None:
    if not shutil.which('docker'):
        return None
    probe = subprocess.run(
        ['docker', 'compose', 'version'],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    return ['docker', 'compose']


def _image_exists(name: str) -> bool:
    result = subprocess.run(
        ['docker', 'image', 'inspect', name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _docker_can_start(log: RunLog) -> bool:
    cmd = [
        'docker',
        'run',
        '--rm',
        '--name',
        f'{COMPOSE_PROJECT}_probe_{os.getpid()}',
        '--entrypoint',
        'redis-server',
        REDIS_IMAGE,
        '--version',
    ]
    try:
        code = _run(cmd, log=log, timeout=45, quiet=True)
    except subprocess.TimeoutExpired:
        log.write(format_console('warning', t('scenario_test_docker_start_hung')))
        return False
    if code != 0:
        log.write(format_console('warning', t('scenario_test_docker_start_hung')))
        return False
    return True


def _compose_cmd(
    compose: list[str],
    run_dir: Path,
    *args: str,
) -> list[str]:
    return [
        *compose,
        '--project-name',
        COMPOSE_PROJECT,
        '--project-directory',
        str(run_dir),
        '-f',
        str(run_dir / 'docker-compose.yml'),
        *args,
    ]


def _run(
    cmd: Sequence[str],
    *,
    log: RunLog,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    quiet: bool = False,
    discard_output: bool = False,
) -> int:
    log.write('cmd: ' + ' '.join(cmd))
    stdout_arg: object = subprocess.DEVNULL if discard_output else subprocess.PIPE
    proc = subprocess.Popen(
        list(cmd),
        stdin=subprocess.DEVNULL,
        stdout=stdout_arg,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    try:
        out, _err = proc.communicate(timeout=timeout)
        code = int(proc.returncode)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc.pid)
        try:
            proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        raise
    if out and not discard_output:
        log.compose_log.parent.mkdir(parents=True, exist_ok=True)
        with log.compose_log.open('a', encoding='utf-8', errors='replace') as fh:
            fh.write('\n--- ' + ' '.join(cmd) + ' ---\n')
            fh.write(out)
            if not out.endswith('\n'):
                fh.write('\n')
        if not quiet or code != 0:
            tail = _redact(out[-4000:].rstrip())
            if tail:
                log.write(tail)
    log.write(f'exit={code}')
    return code


def _prepare_run_dir(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_dir = ensure_dir(cache_dir(root) / 'tmp' / 'scenario-test' / stamp)
    for name in ('logs', 'http'):
        ensure_dir(run_dir / name)
    return run_dir


def _prepare_spec_dir(parent: Path, spec_id: str) -> Path:
    run_dir = ensure_dir(parent / spec_id)
    for name in ('logs', 'logs/docker', 'media', 'notebooks', 'jupyter', 'http', 'static_api', 'modules'):
        ensure_dir(run_dir / name)
    return run_dir


def _remove_containers(names: Sequence[str], log: RunLog) -> None:
    for name in names:
        try:
            _run(['docker', 'rm', '-f', name], log=log, timeout=20, quiet=True)
        except subprocess.TimeoutExpired:
            log.write(f'skip rm hung {name}')
        except OSError as exc:
            log.write(f'down error {name}: {exc}')


def _new_secrets() -> dict[str, str]:
    media_key = secrets.token_hex(32)
    api_secret = secrets.token_hex(32)
    while media_key == api_secret:
        media_key = secrets.token_hex(32)
    return {
        'meili_key': secrets.token_hex(16),
        'jupyter_token': secrets.token_hex(16),
        'api_secret': api_secret,
        'jwt_secret': secrets.token_hex(32),
        'media_key': media_key,
        'bridge_token': secrets.token_hex(32),
    }


def _probe_docker(log: RunLog, root: Path, probe_dir: Path) -> bool:
    compose_bin = _docker()
    if compose_bin is None:
        log.write(format_console('warning', t('scenario_test_no_docker')))
        return False
    if not _image_exists(PYTHON_IMAGE):
        log.write(format_console('warning', t('scenario_test_no_python_image', image=PYTHON_IMAGE)))
        return False
    ports = pick_scenario_ports()
    if ports is None:
        log.write(format_console('warning', t('scenario_test_ports_busy')))
        return False
    write_runtime_env(
        probe_dir / RUNTIME_ENV_NAME,
        ports=ports,
        api_secret='a' * 32,
        jwt_secret='b' * 32,
        media_internal_key='c' * 32,
        meili_key='probe',
        jupyter_token='probe',
    )
    write_nginx_conf(
        probe_dir / 'nginx.conf',
        api_port=ports['api'],
        nginx_port=ports['nginx'],
        jupyter_port=ports['jupyter'],
        media_port=ports['media'],
    )
    write_compose_file(
        probe_dir / 'docker-compose.yml',
        project_root=root,
        run_dir=probe_dir,
        ports=ports,
        meili_key='probe',
        jupyter_token='probe',
        project_name=COMPOSE_PROJECT,
    )
    write_modules_compose(probe_dir / 'modules.generated.yml', root)
    env = sanitized_os_env()
    if _run(_compose_cmd(compose_bin, probe_dir, 'config'), log=log, env=env, timeout=60, quiet=True) != 0:
        log.write(format_console('error', t('scenario_test_compose_config_failed')))
        raise ComposeConfigFailed()
    log.write('compose config ok')
    try:
        modules_data = yaml.safe_load((probe_dir / 'modules.generated.yml').read_text(encoding='utf-8'))
        services = list((modules_data or {}).get('services') or {})
        log.write(f'modules_yaml_ok services={services}')
    except yaml.YAMLError as exc:
        log.write(format_console('warning', t('scenario_test_modules_config_failed')))
        log.write(str(exc))
    return _docker_can_start(log)


def main() -> int:
    parser = argparse.ArgumentParser(description='Isolated deployment scenario runner')
    parser.add_argument('--spec', '--id', action='append', dest='spec_ids', default=None)
    parser.add_argument('--launch', choices=('host', 'docker'), default=None)
    parser.add_argument('--db', choices=('sqlite', 'postgres', 'mysql', 'mssql'), default=None)
    args = parser.parse_args()
    configure_stdio_utf8()
    root = _PROJECT_ROOT.resolve()
    run_dir = _prepare_run_dir(root)
    log = RunLog(run_dir / 'run.log', run_dir / 'compose.log')
    docker_ok: bool | None = None
    outcomes: list[tuple[str, int]] = []
    specs = filter_specs(spec_ids=args.spec_ids, launch=args.launch, db=args.db)
    before = workspace_config_fingerprint(root)
    process_env = sanitized_os_env()
    try:
        log.write(format_console('info', t('scenario_test_run_dir', path=str(run_dir))))
        if not specs:
            log.write(format_console('error', t('scenario_test_failed', path=str(run_dir))))
            return 1
        for spec in specs:
            spec_dir = _prepare_spec_dir(run_dir, spec.id)
            log.write(
                format_console(
                    'info',
                    t(
                        'scenario_test_spec_start',
                        id=spec.id,
                        launch=spec.launch,
                        proxy=spec.proxy,
                        jupyter=spec.jupyter,
                        db=spec.db,
                        broker=spec.broker,
                        module_runtime=spec.module_runtime,
                    ),
                )
            )
            ports = pick_scenario_ports()
            if ports is None:
                log.write(format_console('warning', t('scenario_test_ports_busy')))
                outcomes.append((spec.id, DOCKER_SKIP))
                continue
            log.write(f'ports={ports}')
            secrets_map = _new_secrets()
            if spec.launch == 'docker':
                if docker_ok is None:
                    try:
                        docker_ok = _probe_docker(log, root, spec_dir)
                    except ComposeConfigFailed:
                        log.write(format_console('error', t('scenario_test_failed', path=str(run_dir))))
                        return 1
                if not docker_ok:
                    outcomes.append((spec.id, DOCKER_SKIP))
                    continue
                project = f'{COMPOSE_PROJECT}_{run_dir.name.lower()}_{spec.id}'
                code = run_docker_scenario(
                    project=project,
                    project_root=root,
                    run_dir=spec_dir,
                    spec=spec,
                    ports=ports,
                    api_secret=secrets_map['api_secret'],
                    jwt_secret=secrets_map['jwt_secret'],
                    media_key=secrets_map['media_key'],
                    meili_key=secrets_map['meili_key'],
                    jupyter_token=secrets_map['jupyter_token'],
                    bridge_token=secrets_map['bridge_token'],
                    log=log,
                    run_cmd=_run,
                    env=process_env,
                    remove_containers=_remove_containers,
                )
                if code == DOCKER_SKIP:
                    docker_ok = False
            else:
                code = run_host_scenario(
                    project_root=root,
                    run_dir=spec_dir,
                    spec=spec,
                    ports=ports,
                    api_secret=secrets_map['api_secret'],
                    jwt_secret=secrets_map['jwt_secret'],
                    media_key=secrets_map['media_key'],
                    meili_key=secrets_map['meili_key'],
                    jupyter_token=secrets_map['jupyter_token'],
                    log=log,
                    bridge_token=secrets_map['bridge_token'],
                )
            outcomes.append((spec.id, code))
            if workspace_config_fingerprint(root) != before:
                log.write(format_console(
                    'error',
                    t('scenario_test_host_config_changed', id=spec.id),
                ))
                return 1
        failed = [item_id for item_id, code in outcomes if code == 1]
        for item_id, code in outcomes:
            log.write(f'spec {item_id} result={code}')
        if failed:
            log.write(format_console('error', t('scenario_test_failed', path=str(run_dir))))
            return 1
        log.write(format_console('ok', t('scenario_test_ok', path=str(run_dir))))
        return 0
    except subprocess.TimeoutExpired:
        log.write(format_console('error', t('scenario_test_timeout')))
        return 1
    finally:
        log.close()


if __name__ == '__main__':
    raise SystemExit(main())
