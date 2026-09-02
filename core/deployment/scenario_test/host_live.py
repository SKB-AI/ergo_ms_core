"""Оркестрация живого хостового сценария."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Mapping

from cli_locale import t
from console_tags import format_console
from scenario_test.host_stack import (
    HostBinaryMissing,
    build_host_env,
    close_proc,
    require_host_binaries,
    run_django,
    spawn_python,
    start_throwaway_nginx,
    start_throwaway_postgres,
    start_throwaway_redis,
    stop_throwaway_postgres,
    venv_python,
    write_host_artifacts,
    write_host_nginx_prefix,
)
from scenario_test.jupyter_overlay import (
    apply_python_overlay,
    install_jupyter_overlay,
    jupyterlab_importable,
    python_overlay_dir,
)
from scenario_test.live_checks import run_live_http_checks
from scenario_test.matrix import ScenarioSpec
from scenario_test.sidecars import (
    DockerUnavailable,
    SidecarFailed,
    docker_available,
    start_throwaway_mssql,
    start_throwaway_mysql,
    stop_throwaway_container,
)
from scenario_test.stack import modules_with_bridge

OK = 0
FAIL = 1
SKIP = 2


def run_host_scenario(
    *,
    project_root: Path,
    run_dir: Path,
    spec: ScenarioSpec,
    ports: Mapping[str, int],
    api_secret: str,
    jwt_secret: str,
    media_key: str,
    meili_key: str,
    jupyter_token: str,
    log,
    bridge_token: str = '',
) -> int:
    redis_proc = None
    nginx_proc = None
    api_proc = None
    media_proc = None
    jupyter_proc = None
    module_proc = None
    pg_data = run_dir / 'pgdata'
    mysql_name = f'ergo_ms_scenario_{spec.id}_mysql'
    mssql_name = f'ergo_ms_scenario_{spec.id}_mssql'
    started_mysql = False
    started_mssql = False
    try:
        try:
            require_host_binaries(project_root, spec)
        except HostBinaryMissing as exc:
            log.write(format_console(
                'warning',
                t('scenario_test_skip_host_bin', id=spec.id, name=exc.name),
            ))
            return SKIP
        module_name = ''
        extra: dict[str, str] = {
            'API_SECRET_KEY': api_secret,
            'API_JWT_SIGNING_KEY': jwt_secret,
            'MEDIA_API_INTERNAL_KEY': media_key,
            'API_JUPYTER_TOKEN': jupyter_token,
            'MEILI_MASTER_KEY': meili_key,
        }
        if spec.module_runtime == 'microservice':
            bridges = modules_with_bridge(project_root)
            if not bridges:
                log.write(format_console(
                    'warning',
                    t('scenario_test_skip_no_module', id=spec.id),
                ))
                return SKIP
            module_name = bridges[0]
            extra['MICROSERVICE_MODULES'] = module_name
            extra['MODULE_RUNTIME'] = 'microservice'
            extra['BRIDGE_INTERNAL_TOKEN'] = bridge_token
            extra[module_name.upper().replace('-', '_') + '_PORT'] = str(int(ports['module']))
        if spec.needs_docker_db and not docker_available():
            log.write(format_console(
                'warning',
                t('scenario_test_skip_no_docker', id=spec.id),
            ))
            return SKIP
        write_host_artifacts(
            run_dir=run_dir,
            spec=spec,
            ports=ports,
            project_root=project_root,
            api_secret=api_secret,
            jwt_secret=jwt_secret,
            media_internal_key=media_key,
            meili_key=meili_key,
            jupyter_token=jupyter_token,
            module_name=module_name,
        )
        env = build_host_env(
            project_root=project_root,
            run_dir=run_dir,
            spec=spec,
            ports=ports,
            extra=extra,
        )
        if spec.use_postgres:
            log.write('host postgres start')
            start_throwaway_postgres(
                project_root,
                pg_data,
                int(ports['postgres']),
                run_dir / 'logs' / 'pg_ctl.log',
            )
            log.write('host postgres ready')
        if spec.use_mysql:
            log.write('host mysql sidecar start')
            start_throwaway_mysql(mysql_name, int(ports['mysql']))
            started_mysql = True
            log.write('host mysql sidecar ready')
        if spec.use_mssql:
            log.write('host mssql sidecar start')
            start_throwaway_mssql(mssql_name, int(ports['mssql']))
            started_mssql = True
            log.write('host mssql sidecar ready')
        if spec.use_redis:
            log.write('host redis start')
            redis_proc = start_throwaway_redis(
                project_root,
                run_dir,
                int(ports['redis']),
                run_dir / 'logs' / 'redis.log',
            )
        log.write('host migrate')
        migrate = run_django(
            project_root=project_root,
            env=env,
            command='migrate',
            args=('--noinput',),
            timeout=300,
        )
        if migrate.returncode != 0:
            tail = ((migrate.stdout or '') + (migrate.stderr or ''))[-2000:]
            if tail.strip():
                log.write(tail.strip())
            log.write(format_console('error', t('scenario_test_migrate_failed')))
            return FAIL
        api_proc = spawn_python(
            project_root=project_root,
            script='core/api/scripts/start_api.py',
            env=env,
            log_path=run_dir / 'logs' / 'api.log',
        )
        media_proc = spawn_python(
            project_root=project_root,
            script='core/api/scripts/start_media_api.py',
            env=env,
            log_path=run_dir / 'logs' / 'media.log',
        )
        if module_name:
            module_env = dict(env)
            module_env['API_PORT'] = str(int(ports['module']))
            module_env['MODULE_API_BIND_PORT'] = str(int(ports['module']))
            module_proc = spawn_python(
                project_root=project_root,
                script='core/api/scripts/start_module_api.py',
                env=module_env,
                log_path=run_dir / 'logs' / 'module.log',
                extra_args=('--module', module_name),
            )
        if spec.use_jupyter:
            overlay = python_overlay_dir(run_dir)
            log.write('host jupyter overlay install')
            installed = install_jupyter_overlay(
                python=venv_python(project_root),
                project_root=project_root,
                overlay=overlay,
            )
            if installed.returncode != 0:
                tail = ((installed.stdout or '') + (installed.stderr or ''))[-2000:]
                if tail.strip():
                    log.write(tail.strip())
                log.write(format_console('error', t('scenario_test_jupyter_overlay_failed')))
                return FAIL
            apply_python_overlay(env, overlay)
            if not jupyterlab_importable(venv_python(project_root), overlay):
                log.write(format_console('error', t('scenario_test_jupyter_overlay_failed')))
                return FAIL
            jupyter_proc = spawn_python(
                project_root=project_root,
                script='core/api/scripts/start_jupyter.py',
                env=env,
                log_path=run_dir / 'logs' / 'jupyter.log',
            )
        if spec.use_nginx:
            prefix = write_host_nginx_prefix(
                project_root=project_root,
                run_dir=run_dir,
                ports=ports,
            )
            nginx_proc = start_throwaway_nginx(prefix)
        ok = run_live_http_checks(
            names={},
            ports=ports,
            run_dir=run_dir,
            project_root=project_root,
            jupyter_token=jupyter_token,
            log=log,
            launch='host',
            use_nginx=spec.use_nginx,
            use_jupyter=spec.use_jupyter,
            jupyter_mode=spec.jupyter if spec.use_jupyter else 'none',
            host_env=env,
            module_name=module_name,
        )
        if not ok:
            log.write(format_console('error', t('scenario_test_spec_failed', id=spec.id)))
            return FAIL
        log.write(format_console('ok', t('scenario_test_spec_ok', id=spec.id)))
        return OK
    except HostBinaryMissing as exc:
        log.write(format_console(
            'warning',
            t('scenario_test_skip_host_bin', id=spec.id, name=exc.name),
        ))
        return SKIP
    except DockerUnavailable:
        log.write(format_console(
            'warning',
            t('scenario_test_skip_no_docker', id=spec.id),
        ))
        return SKIP
    except SidecarFailed as exc:
        log.write(str(exc))
        log.write(format_console('error', t('scenario_test_spec_failed', id=spec.id)))
        return FAIL
    except subprocess.TimeoutExpired:
        log.write(format_console('error', t('scenario_test_timeout')))
        return FAIL
    except RuntimeError as exc:
        log.write(str(exc))
        log.write(format_console('error', t('scenario_test_spec_failed', id=spec.id)))
        return FAIL
    finally:
        for proc in (module_proc, jupyter_proc, nginx_proc, api_proc, media_proc, redis_proc):
            close_proc(proc)
        if spec.use_postgres:
            stop_throwaway_postgres(project_root, pg_data)
        if started_mysql:
            stop_throwaway_container(mysql_name)
        if started_mssql:
            stop_throwaway_container(mssql_name)
