"""
Нагрузочный прогон API ERGO MS (Locust).

Перед прогоном создаёт N эфемерных пользователей (JWT + device),
после — удаляет их.

ergoms loadtest [--targets …] [--profile api|pages|mixed] [--users N] …
ergoms loadtest --find-limit [--resume] [--growth exp|linear] …
ergoms loadtest --isolated-db | --docker-isolated …
ergoms loadtest --cleanup-users
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
_LOADTEST_DIR = _DEPLOYMENT_DIR / 'loadtest'
_LOCUSTFILE = _LOADTEST_DIR / 'locustfile.py'

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from loadtest.config import (  # noqa: E402
    default_report_path,
    load_env,
    project_root_from_here,
    resolve_api_host,
)
from loadtest.find_limit import (  # noqa: E402
    DEFAULT_MAX_P95_MS,
    GROWTH_EXP,
    GROWTH_LINEAR,
    find_limit,
)
from loadtest.isolated_db import DEFAULT_LOADTEST_API_PORT  # noqa: E402
from loadtest.isolated_runtime import (  # noqa: E402
    IsolatedSession,
    start_docker_isolated,
    start_host_isolated,
)
from loadtest.resources import (  # noqa: E402
    DEFAULT_MAX_CPU_PERCENT,
    DEFAULT_MAX_ERGO_RAM_MB,
    DEFAULT_MAX_RAM_PERCENT,
)
from loadtest.provision import cleanup_users, ensure_users, provision_users  # noqa: E402
from loadtest_loader import (  # noqa: E402
    apply_profile,
    collect_pages,
    collect_scenarios,
    discover_targets,
    needs_bearer_auth,
    parse_profile_arg,
    parse_targets_arg,
    select_targets,
    targets_payload,
)


def resolve_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir() or not (root / 'pyproject.toml').is_file():
            raise SystemExit(format_console('error', t('project_dir_not_found', root=root)))
        return root
    root = project_root_from_here()
    if (root / 'pyproject.toml').is_file():
        return root
    raise SystemExit(format_console('error', t('project_root_resolve_failed')))


def _ensure_locust() -> None:
    import importlib.util

    if importlib.util.find_spec('locust') is None:
        raise SystemExit(format_console('error', t('loadtest_locust_missing')))


def list_targets(root: Path) -> int:
    all_targets = discover_targets(root)
    print(format_console('info', t('loadtest_available_targets')))
    if not all_targets:
        print(f'  {t("modules_none")}')
        return 0
    for item in all_targets:
        status = t('loadtest_target_enabled') if item.enabled else t('loadtest_target_disabled')
        tags = ', '.join(item.tags) if item.tags else '-'
        print(
            f'  - {item.name}: {len(item.scenarios)} scenarios, '
            f'{len(item.pages)} pages, {status}, tags=[{tags}]'
        )
    return 0


def _prepare_workload(args: argparse.Namespace, root: Path):
    env_map = load_env(root)
    host = resolve_api_host(env_map, explicit=args.host)
    all_targets = discover_targets(root)
    selection = parse_targets_arg(args.targets)
    selected = select_targets(all_targets, selection)
    if not selected:
        print(format_console('error', t('loadtest_no_targets_selected')), file=sys.stderr)
        return None

    scenarios, pages = apply_profile(
        collect_scenarios(selected),
        collect_pages(selected),
        args.profile,
    )
    if not scenarios and not pages:
        print(
            format_console(
                'error',
                t('loadtest_no_workload', profile=args.profile),
            ),
            file=sys.stderr,
        )
        return None
    return host, selected, scenarios, pages


def _cleanup_run(
    root: Path,
    run_id: str | None,
    *,
    env: dict[str, str] | None = None,
) -> None:
    if not run_id:
        return
    print(format_console('info', t('loadtest_cleanup_users', run_id=run_id)))
    try:
        cleanup_users(root, run_id=run_id, env=env)
        print(format_console('ok', t('loadtest_cleanup_ok', run_id=run_id)))
    except RuntimeError as exc:
        print(
            format_console(
                'warning',
                t('loadtest_cleanup_failed', run_id=run_id, detail=str(exc)),
            ),
            file=sys.stderr,
        )


def _isolation_session(args: argparse.Namespace) -> IsolatedSession | None:
    return getattr(args, 'isolation_session', None)


def _setup_isolation(args: argparse.Namespace, root: Path) -> IsolatedSession | None:
    isolated = bool(getattr(args, 'isolated_db', False))
    docker_iso = bool(getattr(args, 'docker_isolated', False))
    if isolated and docker_iso:
        raise RuntimeError(t('loadtest_isolated_mutex'))
    port = int(getattr(args, 'loadtest_api_port', None) or DEFAULT_LOADTEST_API_PORT)
    if isolated:
        print(format_console('info', t('loadtest_isolated_db_start', port=port)))
        session = start_host_isolated(
            root,
            api_port=port,
            drop_db=bool(getattr(args, 'drop_db', False)),
        )
        print(
            format_console(
                'ok',
                t(
                    'loadtest_isolated_db_ready',
                    clone=session.clone_name or '',
                    url=session.base_url,
                ),
            )
        )
        return session
    if docker_iso:
        print(format_console('info', t('loadtest_docker_isolated_start', port=port)))
        session = start_docker_isolated(root, api_port=port)
        print(
            format_console(
                'ok',
                t('loadtest_docker_isolated_ready', url=session.base_url),
            )
        )
        return session
    return None


def run_loadtest(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    prepared = _prepare_workload(args, root)
    if prepared is None:
        return 1
    host, selected, scenarios, pages = prepared
    isolation = _isolation_session(args)
    provision_env = isolation.provision_env if isolation else None
    if isolation is not None:
        host = isolation.base_url
    needs_auth = needs_bearer_auth(scenarios, pages)

    run_id: str | None = None
    access_tokens: list[str] = []
    provision_file: Path | None = None

    if needs_auth:
        print(
            format_console(
                'info',
                t('loadtest_provision_users', count=args.users),
            )
        )
        fd, provision_name = tempfile.mkstemp(
            prefix='ergo_loadtest_users_',
            suffix='.json',
        )
        os.close(fd)
        provision_file = Path(provision_name)
        try:
            payload_users = provision_users(
                root,
                count=args.users,
                out_path=provision_file,
                env=provision_env,
            )
        except RuntimeError as exc:
            print(
                format_console(
                    'error',
                    t('loadtest_provision_failed', detail=str(exc)),
                ),
                file=sys.stderr,
            )
            try:
                provision_file.unlink(missing_ok=True)
            except OSError:
                pass
            return 1

        run_id = str(payload_users.get('run_id') or '')
        access_tokens = [
            str(tok)
            for tok in (payload_users.get('access_tokens') or [])
            if tok
        ]
        if not run_id or len(access_tokens) != args.users:
            print(
                format_console('error', t('loadtest_provision_incomplete')),
                file=sys.stderr,
            )
            return 1
        print(
            format_console(
                'ok',
                t('loadtest_provision_ok', count=len(access_tokens), run_id=run_id),
            )
        )

    _ensure_locust()

    report_path = Path(args.html).resolve() if args.html else default_report_path(root)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        'scenarios': [s.to_runtime_dict() for s in scenarios],
        'pages': [p.to_runtime_dict() for p in pages],
        'targets': [item.name for item in selected],
        'profile': args.profile,
        'access_tokens': access_tokens,
        'run_id': run_id,
    }

    print(
        format_console(
            'info',
            t(
                'loadtest_starting',
                targets=', '.join(item.name for item in selected),
                count=len(scenarios),
                pages=len(pages),
                profile=args.profile,
                host=host,
                users=args.users,
                run_time=args.run_time,
            ),
        )
    )

    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        suffix='.json',
        prefix='ergo_loadtest_',
        delete=False,
    ) as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        scenarios_file = tmp.name

    child_env = os.environ.copy()
    child_env['ERGO_LOADTEST_SCENARIOS_FILE'] = scenarios_file

    cmd = [
        sys.executable,
        '-m',
        'locust',
        '-f',
        str(_LOCUSTFILE),
        '--host',
        host,
        '-u',
        str(args.users),
        '-r',
        str(args.spawn_rate),
        '--html',
        str(report_path),
    ]
    if not args.ui:
        cmd.extend(['--headless', '-t', str(args.run_time)])

    try:
        result = subprocess.run(cmd, cwd=str(root), env=child_env, check=False)
    finally:
        try:
            Path(scenarios_file).unlink(missing_ok=True)
        except OSError:
            pass
        if provision_file is not None:
            try:
                provision_file.unlink(missing_ok=True)
            except OSError:
                pass
        _cleanup_run(root, run_id, env=provision_env)

    if result.returncode == 0:
        print(format_console('ok', t('loadtest_finished', report=report_path)))
    else:
        print(
            format_console(
                'error',
                t('loadtest_failed', code=result.returncode, report=report_path),
            ),
            file=sys.stderr,
        )
    return result.returncode


def run_find_limit(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    prepared = _prepare_workload(args, root)
    if prepared is None:
        return 1
    host, selected, scenarios, pages = prepared
    isolation = _isolation_session(args)
    provision_env = isolation.provision_env if isolation else None
    if isolation is not None:
        host = isolation.base_url
    if not needs_bearer_auth(scenarios, pages):
        print(format_console('error', t('loadtest_find_limit_needs_auth')), file=sys.stderr)
        return 1

    _ensure_locust()

    print(
        format_console(
            'info',
            t(
                'loadtest_find_limit_start',
                growth=args.growth,
                step=(
                    t('loadtest_find_limit_step_auto')
                    if args.growth == GROWTH_EXP
                    else str(args.step_users)
                ),
                step_time=args.step_time,
                max_users=args.max_users if args.max_users else t('loadtest_find_limit_no_max'),
            ),
        )
    )

    run_id_holder: dict[str, str | None] = {'run_id': None}

    def _ensure(
        *,
        total: int,
        out_path: Path,
        run_id: str | None,
        access_tokens: list[str],
    ) -> tuple[str, list[str]]:
        print(
            format_console(
                'info',
                t('loadtest_provision_users', count=total),
            )
        )
        new_run_id, tokens = ensure_users(
            root,
            total=total,
            out_path=out_path,
            run_id=run_id,
            access_tokens=access_tokens,
            env=provision_env,
        )
        run_id_holder['run_id'] = new_run_id
        if len(tokens) > len(access_tokens):
            print(
                format_console(
                    'ok',
                    t(
                        'loadtest_provision_ok',
                        count=len(tokens),
                        run_id=new_run_id,
                    ),
                )
            )
        return new_run_id, tokens

    def _log(level: str, message: str) -> None:
        print(format_console(level, message))

    try:
        result = find_limit(
            root=root,
            host=host,
            scenario_dicts=[s.to_runtime_dict() for s in scenarios],
            page_dicts=[p.to_runtime_dict() for p in pages],
            target_names=[item.name for item in selected],
            ensure_users_fn=_ensure,
            step_users=args.step_users,
            step_time=args.step_time,
            max_fail_ratio=args.max_fail_ratio,
            max_p95_ms=float(args.max_p95_ms),
            max_cpu_percent=float(args.max_cpu_percent),
            max_ram_percent=float(args.max_ram_percent),
            max_ergo_ram_mb=float(args.max_ergo_ram_mb),
            max_users=args.max_users,
            spawn_rate=args.spawn_rate if args.spawn_rate_explicit else None,
            out_dir=root / 'logs' / 'loadtest' / 'find_limit',
            resume=bool(args.resume),
            growth=args.growth,
            log=_log,
        )
    except FileNotFoundError as exc:
        print(
            format_console(
                'error',
                t('loadtest_find_limit_resume_missing', path=str(exc)),
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(
            format_console('error', t('loadtest_find_limit_failed', detail=str(exc))),
            file=sys.stderr,
        )
        _cleanup_run(root, run_id_holder.get('run_id'), env=provision_env)
        return 1

    _cleanup_run(
        root,
        result.run_id or run_id_holder.get('run_id'),
        env=provision_env,
    )

    if result.status == 'broke':
        print(
            format_console(
                'ok',
                t(
                    'loadtest_find_limit_summary_broke',
                    capacity=(
                        result.capacity if result.capacity is not None else 'none'
                    ),
                    broke_at=(
                        result.broke_at if result.broke_at is not None else '?'
                    ),
                    summary=result.summary_path,
                ),
            )
        )
    elif result.status == 'capped':
        print(
            format_console(
                'ok',
                t(
                    'loadtest_find_limit_summary_capped',
                    capacity=result.capacity if result.capacity is not None else args.max_users,
                    summary=result.summary_path,
                ),
            )
        )
    else:
        print(
            format_console(
                'warning',
                t('loadtest_find_limit_summary_empty', summary=result.summary_path),
            )
        )

    if result.status == 'broke' and (result.capacity is None or result.capacity < 1):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description=t('loadtest_description'))
    parser.add_argument('--root', default=None, help=t('help_root_path'))
    parser.add_argument(
        '--targets',
        default='core',
        help=t('loadtest_help_targets'),
    )
    parser.add_argument(
        '--profile',
        default='mixed',
        help=t('loadtest_help_profile'),
    )
    parser.add_argument(
        '--list-targets',
        action='store_true',
        help=t('loadtest_help_list_targets'),
    )
    parser.add_argument(
        '--cleanup-users',
        action='store_true',
        help=t('loadtest_help_cleanup_users'),
    )
    parser.add_argument(
        '--users',
        type=int,
        default=10,
        help=t('loadtest_help_users'),
    )
    parser.add_argument(
        '--spawn-rate',
        type=int,
        default=2,
        help=t('loadtest_help_spawn_rate'),
    )
    parser.add_argument(
        '--run-time',
        default='1m',
        help=t('loadtest_help_run_time'),
    )
    parser.add_argument('--host', default=None, help=t('loadtest_help_host'))
    parser.add_argument('--html', default=None, help=t('loadtest_help_html'))
    parser.add_argument(
        '--ui',
        action='store_true',
        help=t('loadtest_help_ui'),
    )
    parser.add_argument(
        '--find-limit',
        action='store_true',
        help=t('loadtest_help_find_limit'),
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help=t('loadtest_help_resume'),
    )
    parser.add_argument(
        '--max-users',
        type=int,
        default=None,
        help=t('loadtest_help_max_users'),
    )
    parser.add_argument(
        '--growth',
        choices=(GROWTH_EXP, GROWTH_LINEAR),
        default=GROWTH_EXP,
        help=t('loadtest_help_growth'),
    )
    parser.add_argument(
        '--step-users',
        type=int,
        default=1,
        help=t('loadtest_help_step_users'),
    )
    parser.add_argument(
        '--step-time',
        default='30s',
        help=t('loadtest_help_step_time'),
    )
    parser.add_argument(
        '--max-fail-ratio',
        type=float,
        default=0.01,
        help=t('loadtest_help_max_fail_ratio'),
    )
    parser.add_argument(
        '--max-p95-ms',
        type=float,
        default=DEFAULT_MAX_P95_MS,
        help=t('loadtest_help_max_p95_ms'),
    )
    parser.add_argument(
        '--max-cpu-percent',
        type=float,
        default=DEFAULT_MAX_CPU_PERCENT,
        help=t('loadtest_help_max_cpu_percent'),
    )
    parser.add_argument(
        '--max-ram-percent',
        type=float,
        default=DEFAULT_MAX_RAM_PERCENT,
        help=t('loadtest_help_max_ram_percent'),
    )
    parser.add_argument(
        '--max-ergo-ram-mb',
        type=float,
        default=DEFAULT_MAX_ERGO_RAM_MB,
        help=t('loadtest_help_max_ergo_ram_mb'),
    )
    parser.add_argument(
        '--isolated-db',
        action='store_true',
        help=t('loadtest_help_isolated_db'),
    )
    parser.add_argument(
        '--docker-isolated',
        action='store_true',
        help=t('loadtest_help_docker_isolated'),
    )
    parser.add_argument(
        '--drop-db',
        action='store_true',
        help=t('loadtest_help_drop_db'),
    )
    parser.add_argument(
        '--loadtest-api-port',
        type=int,
        default=DEFAULT_LOADTEST_API_PORT,
        help=t('loadtest_help_loadtest_api_port'),
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help=t('help_json_stdout'),
    )
    args = parser.parse_args(argv)

    raw_argv = argv if argv is not None else sys.argv[1:]
    args.spawn_rate_explicit = any(
        a == '--spawn-rate' or a.startswith('--spawn-rate=')
        for a in raw_argv
    )
    args.step_users_explicit = any(
        a == '--step-users' or a.startswith('--step-users=')
        for a in raw_argv
    )

    try:
        args.profile = parse_profile_arg(args.profile)
    except ValueError:
        print(format_console('error', t('loadtest_profile_invalid')), file=sys.stderr)
        return 1

    if args.find_limit and args.ui:
        print(format_console('error', t('loadtest_find_limit_no_ui')), file=sys.stderr)
        return 1
    if args.resume and not args.find_limit:
        print(format_console('error', t('loadtest_resume_requires_find_limit')), file=sys.stderr)
        return 1
    if args.users < 1:
        print(format_console('error', t('loadtest_users_min')), file=sys.stderr)
        return 1
    if args.spawn_rate < 1:
        print(format_console('error', t('loadtest_spawn_rate_min')), file=sys.stderr)
        return 1
    if args.find_limit:
        growth = str(args.growth or GROWTH_EXP).strip().lower()
        if growth not in (GROWTH_EXP, GROWTH_LINEAR):
            print(format_console('error', t('loadtest_growth_invalid')), file=sys.stderr)
            return 1
        args.growth = growth
        if args.step_users < 1:
            print(format_console('error', t('loadtest_step_users_min')), file=sys.stderr)
            return 1
        if growth == GROWTH_EXP and args.step_users_explicit:
            print(
                format_console('warning', t('loadtest_step_users_ignored_for_exp')),
            )
        if args.max_users is not None and args.max_users < 1:
            print(format_console('error', t('loadtest_max_users_min')), file=sys.stderr)
            return 1
        if args.max_fail_ratio < 0 or args.max_fail_ratio > 1:
            print(format_console('error', t('loadtest_fail_ratio_range')), file=sys.stderr)
            return 1
        if args.max_p95_ms <= 0:
            print(format_console('error', t('loadtest_p95_min')), file=sys.stderr)
            return 1
        if args.max_cpu_percent < 0 or args.max_cpu_percent > 100:
            print(format_console('error', t('loadtest_cpu_percent_range')), file=sys.stderr)
            return 1
        if args.max_ram_percent < 0 or args.max_ram_percent > 100:
            print(format_console('error', t('loadtest_ram_percent_range')), file=sys.stderr)
            return 1
        if args.max_ergo_ram_mb < 0:
            print(format_console('error', t('loadtest_ergo_ram_min')), file=sys.stderr)
            return 1

    root = resolve_root(args.root)

    if args.cleanup_users:
        print(format_console('info', t('loadtest_cleanup_all_users')))
        try:
            cleanup_users(root)
        except RuntimeError as exc:
            print(
                format_console(
                    'error',
                    t('loadtest_cleanup_all_failed', detail=str(exc)),
                ),
                file=sys.stderr,
            )
            return 1
        print(format_console('ok', t('loadtest_cleanup_all_ok')))
        return 0

    if args.drop_db and not args.isolated_db:
        print(format_console('error', t('loadtest_drop_db_requires_isolated')), file=sys.stderr)
        return 1
    if args.loadtest_api_port < 1 or args.loadtest_api_port > 65535:
        print(format_console('error', t('loadtest_api_port_range')), file=sys.stderr)
        return 1

    if args.list_targets:
        if args.json:
            print(json.dumps(targets_payload(discover_targets(root)), ensure_ascii=False))
            return 0
        return list_targets(root)

    if args.json and not args.list_targets and not args.find_limit:
        selection = parse_targets_arg(args.targets)
        selected = select_targets(discover_targets(root), selection)
        scenarios, pages = apply_profile(
            collect_scenarios(selected),
            collect_pages(selected),
            args.profile,
        )
        print(
            json.dumps(
                {
                    'host': resolve_api_host(load_env(root), explicit=args.host),
                    'targets': [item.name for item in selected],
                    'profile': args.profile,
                    'scenarios': [s.to_runtime_dict() for s in scenarios],
                    'pages': [p.to_runtime_dict() for p in pages],
                },
                ensure_ascii=False,
            )
        )
        return 0

    isolation: IsolatedSession | None = None
    args.isolation_session = None
    try:
        try:
            isolation = _setup_isolation(args, root)
        except Exception as exc:  # noqa: BLE001
            print(
                format_console('error', t('loadtest_isolated_failed', detail=str(exc))),
                file=sys.stderr,
            )
            return 1
        args.isolation_session = isolation
        if args.find_limit:
            return run_find_limit(args)
        return run_loadtest(args)
    finally:
        if isolation is not None:
            try:
                isolation.close()
                print(format_console('ok', t('loadtest_isolated_closed')))
            except Exception as exc:  # noqa: BLE001
                print(
                    format_console(
                        'warning',
                        t('loadtest_isolated_close_failed', detail=str(exc)),
                    ),
                    file=sys.stderr,
                )


if __name__ == '__main__':
    raise SystemExit(main())
