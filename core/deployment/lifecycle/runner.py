"""Единая точка входа lifecycle pipeline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_LIFECYCLE_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _LIFECYCLE_DIR.parent
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import (  # noqa: E402
    clear_locale_caches,
    ensure_project_env_loaded,
    resolve_cli_language,
    t,
)
from console_tags import format_console  # noqa: E402

from lifecycle.context import HostPlatform  # noqa: E402
from lifecycle.host import ops as host_ops  # noqa: E402
from lifecycle.host.privilege import needs_sudo_reexec, reexec_with_sudo  # noqa: E402
from lifecycle.orchestrator import DeploymentOrchestrator  # noqa: E402
from lifecycle.recipes import RECIPE_REGISTRY  # noqa: E402


def detect_project_root() -> Path:
    env_root = os.environ.get('ERGOMS_PROJECT_ROOT', '').strip()
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / 'core' / 'deployment').is_dir():
            return candidate
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / 'core' / 'deployment').is_dir():
            return candidate
    return _PROJECT_ROOT.resolve()


def _init_cli_locale(project_root: Path | None = None) -> Path:
    """Подтянуть ERGO_CLI_LANGUAGE из .env до любых t() в шагах setup."""
    if project_root is not None:
        hinted = project_root.resolve()
        if (hinted / 'core' / 'deployment').is_dir():
            root = hinted
        else:
            root = detect_project_root()
    else:
        root = detect_project_root()
    ensure_project_env_loaded(root)
    clear_locale_caches()
    resolve_cli_language(project_root=root)
    return root


def runner_python_argv(project_root: Path, recipe: str) -> list[str]:
    if host_ops.venv_exists(project_root, HostPlatform.current()):
        py = host_ops.venv_python_exe(project_root, HostPlatform.current())
        return [str(py), str(_LIFECYCLE_DIR / 'runner.py')]
    return [*host_ops.base_python_argv(project_root, HostPlatform.current()), str(_LIFECYCLE_DIR / 'runner.py')]


def sudo_reexec_argv(
    py_argv: list[str],
    args: argparse.Namespace,
    extra: list[str],
    *,
    docker_mode: str | None,
) -> list[str]:
    """Повторить разобранные флаги runner при sudo — иначе --with-postgres и порт теряются."""
    sudo_argv = [*py_argv, args.recipe]
    flag_presence = (
        ('recreate_venv', '--recreate-venv'),
        ('purge', '--purge'),
        ('dry_run', '--dry-run'),
        ('force', '--force'),
        ('with_postgres', '--with-postgres'),
    )
    for attr, flag in flag_presence:
        if getattr(args, attr):
            sudo_argv.append(flag)
    if docker_mode:
        sudo_argv.extend(['--docker-mode', docker_mode])
    value_flags = (
        ('worker', '--worker'),
        ('server_name', '--server-name'),
        ('listen_port', '--listen-port'),
        ('domain', '--domain'),
        ('email', '--email'),
        ('source_port', '--source-port'),
        ('source_host', '--source-host'),
        ('source_user', '--source-user'),
        ('source_password', '--source-password'),
    )
    for attr, flag in value_flags:
        value = getattr(args, attr)
        if value:
            sudo_argv.extend([flag, str(value)])
    sudo_argv.extend(extra)
    return sudo_argv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='ErgoMS lifecycle runner')
    parser.add_argument('recipe', nargs='?', help=t('runner_help_recipe'))
    parser.add_argument('--list', action='store_true', help=t('runner_help_list'))
    parser.add_argument('--recreate-venv', action='store_true')
    parser.add_argument('--docker-mode', choices=('dev', 'prod'), default=None)
    parser.add_argument('--purge', action='store_true')
    parser.add_argument('--worker', default='')
    parser.add_argument('--server-name', default='')
    parser.add_argument('--listen-port', default='')
    parser.add_argument('--domain', default='')
    parser.add_argument('--email', default='')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true',
                        help=t('runner_help_force'))
    parser.add_argument('--source-port', default='',
                        help=t('runner_help_source_port'))
    parser.add_argument('--source-host', default='',
                        help=t('runner_help_source_host'))
    parser.add_argument('--source-user', default='',
                        help=t('runner_help_source_user'))
    parser.add_argument('--source-password', default='',
                        help=t('runner_help_source_password'))
    parser.add_argument('--with-postgres', action='store_true',
                        help=t('runner_help_with_postgres'))
    parser.add_argument('--mode', choices=('dev', 'prod'), default=None, help=t('runner_help_mode_alias'))
    parser.add_argument('--project-root', default='', help=t('runner_help_project_root'))
    return parser


def _peel_project_root(argv: list[str]) -> tuple[Path | None, list[str]]:
    leftover = list(argv)
    if '--project-root' not in leftover:
        return None, leftover
    index = leftover.index('--project-root')
    if index + 1 >= len(leftover):
        return None, leftover
    raw = leftover[index + 1]
    del leftover[index:index + 2]
    return Path(raw), leftover


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    root_hint, rest = _peel_project_root(list(argv) if argv is not None else sys.argv[1:])
    project_root = _init_cli_locale(root_hint)

    parser = build_parser()
    args, extra = parser.parse_known_args(rest)

    if args.list:
        for name in sorted(RECIPE_REGISTRY):
            spec = RECIPE_REGISTRY[name]
            print(f'{name}\t{spec.target}\t{spec.description}')
        return 0

    if not args.recipe:
        parser.print_help()
        return 1

    spec = RECIPE_REGISTRY.get(args.recipe)
    if spec is None:
        print(format_console('error', t('unknown_recipe', name=args.recipe)), file=sys.stderr)
        return 1

    docker_mode = args.docker_mode or args.mode
    options: dict = {}
    if args.recreate_venv:
        options['recreate_venv'] = True
    if args.purge:
        options['purge'] = True
    if args.worker:
        options['worker'] = args.worker
    if args.server_name:
        options['server_name'] = args.server_name
    if args.listen_port:
        options['listen_port'] = args.listen_port
    if args.domain:
        options['domain'] = args.domain
    if args.email:
        options['email'] = args.email
    if args.dry_run:
        options['dry_run'] = True
    if args.force:
        options['force'] = True
    if args.source_port:
        options['source_port'] = args.source_port
    if args.source_host:
        options['source_host'] = args.source_host
    if args.source_user:
        options['source_user'] = args.source_user
    if args.source_password:
        options['source_password'] = args.source_password
    if args.with_postgres:
        options['with_postgres'] = True
    if extra:
        options['compose_extra_args'] = extra

    if needs_sudo_reexec(spec.needs_sudo):
        py_argv = runner_python_argv(project_root, args.recipe)
        return reexec_with_sudo(
            sudo_reexec_argv(py_argv, args, extra, docker_mode=docker_mode),
            cwd=project_root,
        )

    orchestrator = DeploymentOrchestrator(project_root)
    return orchestrator.run_recipe(
        args.recipe,
        runtime=spec.runtime,  # type: ignore[arg-type]
        docker_mode=docker_mode,
        options=options,
        extra_services=extra if args.recipe in ('docker-up', 'docker-init') else None,
    )


if __name__ == '__main__':
    raise SystemExit(main())
