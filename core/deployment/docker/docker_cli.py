"""
Обёртка ergoms docker * — Docker Compose для ERGO MS.

Тонкий CLI: операции compose — lifecycle.docker.ops, init/bootstrap — DeploymentOrchestrator.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DOCKER_DIR = Path(__file__).resolve().parent
DEPLOYMENT_DIR = DOCKER_DIR.parent
PROJECT_ROOT = DEPLOYMENT_DIR.parent.parent

if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))
if str(DOCKER_DIR) not in sys.path:
    sys.path.insert(0, str(DOCKER_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from env_resolvers import load_merged_env  # noqa: E402

from lifecycle.docker import ops as docker_ops  # noqa: E402
from lifecycle.orchestrator import DeploymentOrchestrator  # noqa: E402
from docker_stats import add_stats_arguments, run_stats  # noqa: E402


def resolve_cli_project_root() -> Path:
    env_root = os.environ.get('ERGOMS_PROJECT_ROOT', '').strip()
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / 'core' / 'deployment').is_dir():
            return candidate
    return PROJECT_ROOT.resolve()


def cmd_up(args: argparse.Namespace) -> int:
    root = resolve_cli_project_root()
    orchestrator = DeploymentOrchestrator(root)
    return orchestrator.run_recipe(
        'docker-up',
        runtime='docker',
        docker_mode=args.mode,
        extra_services=list(args.extra),
    )

def cmd_down(args: argparse.Namespace) -> int:
    return DeploymentOrchestrator(resolve_cli_project_root()).run_recipe(
        'docker-down',
        runtime='docker',
        docker_mode=args.mode,
        options={'compose_extra_args': list(args.extra)},
    )

def cmd_ps(args: argparse.Namespace) -> int:
    return DeploymentOrchestrator(resolve_cli_project_root()).run_recipe(
        'docker-ps',
        runtime='docker',
        docker_mode=args.mode,
        options={'compose_extra_args': list(args.extra)},
    )

def cmd_logs(args: argparse.Namespace) -> int:
    extra = ['-f'] if args.follow else []
    if args.service:
        extra.append(args.service)
    return DeploymentOrchestrator(resolve_cli_project_root()).run_recipe(
        'docker-logs',
        runtime='docker',
        docker_mode=args.mode,
        options={'compose_extra_args': extra},
    )

def cmd_build(args: argparse.Namespace, *, skip_if_present: bool = False) -> int:
    root = resolve_cli_project_root()
    orchestrator = DeploymentOrchestrator(root)
    if skip_if_present and docker_ops.should_skip_build(load_merged_env(root)):
        print(format_console('skip', t('docker_images_already_built')))
        return 0
    extra = list(args.extra or [])
    options = {'build_extra_args': extra, 'compose_extra_args': extra} if extra else None
    return orchestrator.run_recipe(
        'docker-build',
        runtime='docker',
        docker_mode=args.mode,
        options=options,
    )

def cmd_exec_api_shell(_: argparse.Namespace) -> int:
    if not docker_ops.find_docker_compose():
        print(format_console('error', t('docker_not_found_dot')), file=sys.stderr)
        return 1
    cmd, cwd = docker_ops.build_compose_cmd('exec', extra_args=['api', 'bash'])
    return subprocess.call(cmd, cwd=str(cwd))

def cmd_install_deps(args: argparse.Namespace | None = None) -> int:
    mode = getattr(args, 'mode', None) if args is not None else None
    return DeploymentOrchestrator(resolve_cli_project_root()).docker_install_deps(docker_mode=mode)

def cmd_install_npm_deps(args: argparse.Namespace | None = None) -> int:
    mode = getattr(args, 'mode', None) if args is not None else None
    return DeploymentOrchestrator(resolve_cli_project_root()).docker_install_npm(docker_mode=mode)

def cmd_migrate(args: argparse.Namespace | None = None) -> int:
    mode = getattr(args, 'mode', None) if args is not None else None
    return DeploymentOrchestrator(resolve_cli_project_root()).docker_migrate(docker_mode=mode)

def _confirm_docker_clean(*, assume_yes: bool) -> bool:
    if assume_yes:
        return True

    print(format_console('warning', t('docker_clean_confirm_msg')))

    if not sys.stdin.isatty():
        print(
            format_console(
                'error',
                t('interactive_confirm_unavailable'),
            ),
            file=sys.stderr,
        )
        return False

    try:
        answer = input(t('continue_yn')).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        print(format_console('info', t('cleanup_cancelled')))
        return False

    if answer in ('y', 'yes'):
        return True

    print(format_console('info', t('cleanup_cancelled')))
    return False

def cmd_clean(args: argparse.Namespace) -> int:
    if not _confirm_docker_clean(assume_yes=args.yes):
        return 1

    if not docker_ops.find_docker_compose():
        print(format_console('error', t('docker_not_found_install')), file=sys.stderr)
        return 1

    root = resolve_cli_project_root()
    print(format_console('info', t('docker_clean_stopping')))
    cmd, cwd = docker_ops.build_compose_cmd(
        'down',
        extra_args=['--remove-orphans', '-v', '--rmi', 'local'],
        for_clean=True,
    )
    code = subprocess.call(cmd, cwd=str(cwd))
    if code != 0:
        print(format_console('warning', t('docker_compose_down_warn', code=code)))

    print(format_console('info', t('removing_compose_artifacts')))
    docker_ops.remove_compose_artifacts(root)
    docker_ops.clear_setup_marker(root)
    print(format_console('ok', t('docker_stack_fully_removed')))
    return 0

def cmd_init(args: argparse.Namespace) -> int:
    extra = list(args.extra or [])
    options = {'build_extra_args': extra} if extra else None
    return DeploymentOrchestrator(resolve_cli_project_root()).run_recipe(
        'docker-init',
        runtime='docker',
        docker_mode=args.mode,
        extra_services=extra,
        options=options,
    )

def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description='ErgoMS Docker CLI')
    parser.add_argument('--mode', choices=('dev', 'prod'), default=None)
    sub = parser.add_subparsers(dest='command', required=True)

    for name, handler in (
        ('up', cmd_up),
        ('down', cmd_down),
        ('ps', cmd_ps),
        ('build', cmd_build),
        ('init', cmd_init),
        ('migrate', cmd_migrate),
        ('install-deps', cmd_install_deps),
        ('install-npm', cmd_install_npm_deps),
        ('gen-workers', lambda a: docker_ops.run_generate_workers()),
        ('gen-modules', lambda a: docker_ops.run_generate_modules()),
        ('shell-api', cmd_exec_api_shell),
    ):
        p = sub.add_parser(name)
        p.set_defaults(handler=handler)
        if name in ('up', 'down', 'ps', 'build', 'init'):
            p.add_argument('extra', nargs='*', default=[])

    clean_p = sub.add_parser('clean')
    clean_p.add_argument(
        '--yes',
        action='store_true',
        help=t('docker_clean_help_yes'),
    )
    clean_p.set_defaults(handler=cmd_clean)

    logs_p = sub.add_parser('logs')
    logs_p.add_argument('service', nargs='?', default='')
    logs_p.add_argument('-f', '--follow', action='store_true')
    logs_p.set_defaults(handler=cmd_logs)

    stats_p = sub.add_parser('stats')
    add_stats_arguments(stats_p)
    stats_p.set_defaults(handler=run_stats)

    args = parser.parse_args()
    return args.handler(args)

if __name__ == '__main__':
    raise SystemExit(main())
