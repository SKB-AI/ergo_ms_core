"""Живые HTTP-проверки изолированного стека (docker exec или loopback хоста)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Mapping

from cli_locale import t
from console_tags import format_console
from scenario_test.host_stack import venv_python
from scenario_test.http_checks import (
    extract_asset_paths,
    http_request_direct,
    jupyter_probe_paths,
    load_core_http_cases,
    parse_probe_output,
    parse_ready_json,
    parse_wget_status,
    save_http_dump,
    write_probe_script,
)
from scenario_test.live_stack import container_running, exec_command

_JUPYTER_OK = {200, 302, 401, 403}
_CORE_SCENARIOS = (
    Path(__file__).resolve().parents[1] / 'loadtest' / 'core_scenarios.yaml'
)


def _provision_token_docker(api_container: str, run_dir: Path, log) -> str:
    code = subprocess.run(
        exec_command(
            api_container,
            'poetry',
            'run',
            'python',
            '-c',
            (
                'import sys; sys.path.insert(0, "/app/core/api"); '
                'from commands.base import PoetryCommand; '
                'raise SystemExit(PoetryCommand.for_django("loadtest_provision_users")'
                '.run("--count", "1", "--out", "/app/logs/scenario-tokens.json"))'
            ),
            workdir='/app',
        ),
        capture_output=True,
        timeout=180,
        check=False,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    log.write('cmd: docker exec api loadtest_provision_users')
    if code.returncode != 0:
        tail = ((code.stdout or '') + (code.stderr or ''))[-1500:]
        if tail.strip():
            log.write(tail.strip())
        return ''
    return _read_token(run_dir)


def _provision_token_host(project_root: Path, run_dir: Path, env: Mapping[str, str], log) -> str:
    python = venv_python(project_root)
    api_dir = project_root / 'core' / 'api'
    out = (run_dir / 'logs' / 'scenario-tokens.json').as_posix()
    code = (
        'import sys; sys.path.insert(0, r"{api}"); '
        'from commands.base import PoetryCommand; '
        'raise SystemExit(PoetryCommand.for_django("loadtest_provision_users")'
        '.run("--count", "1", "--out", r"{out}"))'
    ).format(api=str(api_dir), out=out)
    log.write('cmd: host loadtest_provision_users')
    result = subprocess.run(
        [str(python), '-c', code],
        cwd=str(api_dir),
        env=dict(env),
        capture_output=True,
        timeout=180,
        check=False,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if result.returncode != 0:
        tail = ((result.stdout or '') + (result.stderr or ''))[-1500:]
        if tail.strip():
            log.write(tail.strip())
        return ''
    return _read_token(run_dir)


def _read_token(run_dir: Path) -> str:
    payload_path = run_dir / 'logs' / 'scenario-tokens.json'
    if not payload_path.is_file():
        return ''
    try:
        data = json.loads(payload_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return ''
    tokens = data.get('access_tokens') or []
    if not tokens:
        return ''
    return str(tokens[0])


def run_live_http_checks(
    *,
    names: Mapping[str, str],
    ports: Mapping[str, int],
    run_dir: Path,
    project_root: Path,
    jupyter_token: str,
    log,
    launch: str = 'docker',
    use_nginx: bool = True,
    use_jupyter: bool = True,
    jupyter_mode: str = 'nginx',
    host_env: Mapping[str, str] | None = None,
    module_name: str = '',
) -> bool:
    dump = run_dir / 'http'
    api_port = str(ports['api'])
    nginx_port = str(ports['nginx'])
    jupyter_port = str(ports['jupyter'])
    write_probe_script(run_dir / 'logs' / 'http_probe.py')
    failed = False
    host = launch == 'host'

    def probe_api(
        path: str,
        *,
        method: str = 'GET',
        token: str = '',
        json_body: dict | None = None,
    ) -> tuple[int, bytes]:
        if host:
            headers = {'Authorization': f'Bearer {token}'} if token else None
            return http_request_direct(
                f'http://127.0.0.1:{api_port}{path}',
                method=method,
                headers=headers,
                json_body=json_body,
            )
        extra: dict[str, str] = {}
        if token:
            extra['PROBE_AUTH'] = f'Bearer {token}'
        if json_body is not None:
            extra['PROBE_JSON'] = json.dumps(json_body)
        cmd = exec_command(
            names['api'],
            'python',
            '/app/logs/http_probe.py',
            f'http://127.0.0.1:{api_port}{path}',
            method,
            extra_env=extra,
        )
        log.write(f'cmd: docker exec api probe {method} {path}')
        result = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        return parse_probe_output(result.stdout or b'')

    ready_path = '/api/system/ready/'
    code, body = 0, b''
    for _ in range(40):
        try:
            code, body = probe_api(ready_path)
        except (subprocess.TimeoutExpired, OSError):
            code, body = 0, b''
        if code == 200:
            break
        time.sleep(3)
    save_http_dump(dump, 'ready', code, body)
    log.write(f'GET api {ready_path} -> {code}')
    if code != 200 or not parse_ready_json(body):
        log.write(format_console('error', t('scenario_test_ready_failed')))
        return False

    def fetch_nginx(path: str, dump_name: str) -> tuple[int, bytes]:
        if host:
            status, payload = http_request_direct(f'http://127.0.0.1:{nginx_port}{path}')
            save_http_dump(dump, dump_name, status, payload)
            return status, payload
        dest = f'/var/log/ergo/{dump_name}.body'
        cmd = exec_command(
            names['nginx'],
            'wget',
            '-O',
            dest,
            f'http://127.0.0.1:{nginx_port}{path}',
        )
        log.write('cmd: docker exec nginx wget ' + path.split('?', 1)[0])
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=25, check=False, text=True)
        except subprocess.TimeoutExpired:
            save_http_dump(dump, dump_name, 0, b'')
            return 0, b''
        combined = (result.stderr or '') + '\n' + (result.stdout or '')
        status = 200 if result.returncode == 0 else parse_wget_status(combined)
        if status == 0 and combined.strip():
            log.write(combined.strip()[-800:])
        body_path = run_dir / 'logs' / f'{dump_name}.body'
        body_bytes = body_path.read_bytes() if body_path.is_file() else b''
        save_http_dump(dump, dump_name, status, body_bytes)
        return status, body_bytes

    if use_nginx:
        ncode = 0
        for _ in range(40):
            if not host and not container_running(names['nginx']):
                break
            ncode, _nbody = fetch_nginx('/api/system/ready/', 'nginx_ready')
            if ncode == 200:
                break
            time.sleep(3)
        log.write(f'GET nginx /api/system/ready/ -> {ncode}')
        if ncode != 200:
            failed = True

        hcode, _hbody = fetch_nginx('/health/', 'health')
        log.write(f'GET nginx /health/ -> {hcode}')
        if hcode not in {403, 401, 200}:
            failed = True

        dist_index = project_root / 'core' / 'client' / 'dist' / 'index.html'
        if dist_index.is_file():
            for page in ('/', '/login'):
                dump_name = 'root' if page == '/' else 'page_login'
                pcode, pbody = fetch_nginx(page, dump_name)
                log.write(f'GET nginx {page} -> {pcode}')
                if pcode != 200:
                    failed = True
                    continue
                for asset in extract_asset_paths(pbody)[:8]:
                    acode, _abody = fetch_nginx(asset, 'asset_' + asset.strip('/').replace('/', '_'))
                    log.write(f'GET nginx {asset} -> {acode}')
                    if acode == 404:
                        failed = True
        else:
            log.write(format_console('warning', t('scenario_test_no_dist')))

        ucode = 0
        for _ in range(20):
            ucode, _ubody = fetch_nginx('/upload/', 'upload')
            if ucode not in {0, 502, 504}:
                break
            time.sleep(2)
        log.write(f'GET nginx /upload/ -> {ucode}')
        if ucode in {0, 502, 504}:
            failed = True
        if module_name:
            mcode, _mbody = fetch_nginx(f'/api/{module_name}/', 'module_root')
            log.write(f'GET nginx /api/{module_name}/ -> {mcode}')
            if mcode in {0, 502, 504}:
                failed = True

    if host:
        token = _provision_token_host(project_root, run_dir, host_env or {}, log)
    else:
        token = _provision_token_docker(names['api'], run_dir, log)
    if token:
        cases = load_core_http_cases(_CORE_SCENARIOS)
        log.write(f'core_scenarios cases={len(cases)}')
        for case in cases:
            case_token = token if case.auth == 'bearer' else ''
            jcode, jbody = probe_api(
                case.path,
                method=case.method,
                token=case_token,
                json_body=case.json_body,
            )
            save_http_dump(dump, case.case_id, jcode, jbody)
            log.write(f'{case.method} api {case.path} ({case.case_id}) -> {jcode}')
            if jcode not in case.expect_status:
                failed = True
    else:
        log.write(format_console('warning', t('scenario_test_no_jwt')))
        failed = True

    if not use_jupyter:
        return not failed

    jlab = 0
    jupyter_label = '/jupyter/lab'
    for _ in range(120):
        if not host and not container_running(names.get('jupyter', '')):
            break
        for label, url in jupyter_probe_paths(jupyter_port, jupyter_token):
            if host:
                jlab, rest = http_request_direct(url)
            else:
                cmd = exec_command(names['jupyter'], 'python', '/app/logs/http_probe.py', url)
                log.write('cmd: docker exec jupyter probe ' + label)
                try:
                    result = subprocess.run(cmd, capture_output=True, timeout=20, check=False)
                except (subprocess.TimeoutExpired, OSError):
                    continue
                jlab, rest = parse_probe_output(result.stdout or b'')
            if jlab in _JUPYTER_OK:
                jupyter_label = label
                save_http_dump(dump, 'jupyter', jlab, rest)
                break
        if jlab in _JUPYTER_OK:
            break
        time.sleep(5)
    log.write(f'GET jupyter {jupyter_label} -> {jlab}')
    if jlab not in _JUPYTER_OK:
        if not host:
            try:
                captured = subprocess.run(
                    ['docker', 'logs', '--tail', '80', names['jupyter']],
                    capture_output=True,
                    timeout=20,
                    check=False,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                )
                jupyter_logs = (captured.stdout or '') + (captured.stderr or '')
                if jupyter_logs.strip():
                    log.write(jupyter_logs.strip()[-2000:])
            except subprocess.TimeoutExpired:
                pass
        log.write(format_console('error', t('scenario_test_jupyter_failed')))
        failed = True
    elif token and use_nginx and jupyter_mode == 'nginx':
        nginx_jupyter = f'/jupyter/lab?token={jupyter_token}'
        njcode, _njbody = fetch_nginx(nginx_jupyter, 'nginx_jupyter')
        log.write(f'GET nginx /jupyter/lab -> {njcode}')
        if njcode not in _JUPYTER_OK:
            failed = True

    return not failed
