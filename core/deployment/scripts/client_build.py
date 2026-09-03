"""ergoms client-build — оболочка и/или federated remotes по конфигу хоста."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEPLOYMENT_DIR = SCRIPTS_DIR.parent
PROJECT_ROOT = DEPLOYMENT_DIR.parent.parent

for _entry in (str(SCRIPTS_DIR), str(DEPLOYMENT_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from env_file_loader import load_project_env  # noqa: E402
from lifecycle.client_build_plan import (  # noqa: E402
    resolve_client_build_plan,
    should_reload_nginx_after_client_build,
)
from project_layout import (  # noqa: E402
    nodejs_bin_dir,
    nodejs_exe,
    npm_exe,
    npm_root_dir,
    tool_cache_environ,
)

REMOTE_SCRIPT_REL = Path('core/client/scripts/build-client-remote.js')
NPM_WORKSPACE = '@ergo-ms/core-client'


def _merged_env(project_root: Path) -> dict[str, str]:
    values = dict(load_project_env(project_root))
    for key, val in os.environ.items():
        if val is not None and str(val).strip() != '':
            values[key] = str(val).strip()
    return values


def _process_env(project_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(tool_cache_environ(project_root))
    node_bin = nodejs_bin_dir(project_root)
    if node_bin.is_dir():
        sep = ';' if os.name == 'nt' else ':'
        env['PATH'] = f'{node_bin}{sep}{env.get("PATH", "")}'
    return env


def resolve_node(project_root: Path) -> Path:
    portable = nodejs_exe(project_root)
    if portable.is_file():
        return portable
    return Path('node')


def run_shell_build(project_root: Path) -> int:
    npm_cmd = str(npm_exe(project_root))
    if not Path(npm_cmd).is_file():
        npm_cmd = 'npm.cmd' if os.name == 'nt' else 'npm'
    npm_root = npm_root_dir(project_root)
    pkg = npm_root / 'package.json'
    if not pkg.is_file():
        print(format_console('error', t('package_json_not_found_npm')), file=sys.stderr)
        return 1
    print(format_console('info', t('client_build_plan_shell')))
    return subprocess.call(
        [npm_cmd, 'run', 'build', '-w', NPM_WORKSPACE],
        cwd=str(npm_root),
        env=_process_env(project_root),
    )


def run_remote_build(project_root: Path, module_name: str) -> int:
    script = project_root / REMOTE_SCRIPT_REL
    if not script.is_file():
        print(
            format_console('error', t('script_not_found', script_rel=str(REMOTE_SCRIPT_REL))),
            file=sys.stderr,
        )
        return 1
    print(format_console('info', t('client_build_plan_remote', name=module_name)))
    node = resolve_node(project_root)
    return subprocess.call(
        [str(node), str(script), f'--module={module_name}'],
        cwd=str(project_root),
        env=_process_env(project_root),
    )


def reload_nginx_after_build(project_root: Path, environ: dict[str, str]) -> int:
    if not should_reload_nginx_after_client_build(environ):
        return 0
    from lifecycle.services.backends.nginx_backend import _nginx_installed, cmd_reload

    if not _nginx_installed(project_root):
        print(format_console('skip', t('client_build_skip_nginx_not_installed')))
        return 0
    print(format_console('info', t('client_build_nginx_reload')))
    return cmd_reload(project_root)


def execute_client_build(
    project_root: Path,
    environ: dict[str, str],
    *,
    only_modules: list[str] | None = None,
    skip_nginx_reload: bool = False,
) -> int:
    plan = resolve_client_build_plan(
        project_root,
        environ,
        only_modules=only_modules,
    )
    remotes_label = ', '.join(plan.remotes) if plan.remotes else t('client_build_none')
    print(
        format_console(
            'info',
            t(
                'client_build_plan_summary',
                shell=t('client_build_flag_yes') if plan.shell else t('client_build_flag_no'),
                remotes=remotes_label,
            ),
        )
    )
    if plan.is_empty():
        print(format_console('skip', t('client_build_skip_empty')))
        return 0
    if not plan.shell:
        print(format_console('skip', t('client_build_skip_shell')))
    if plan.shell:
        code = run_shell_build(project_root)
        if code != 0:
            return code
    for name in plan.remotes:
        code = run_remote_build(project_root, name)
        if code != 0:
            return code
    if skip_nginx_reload:
        return 0
    return reload_nginx_after_build(project_root, environ)


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description='Build client shell and/or federated remotes')
    parser.add_argument(
        '--module',
        action='append',
        default=[],
        metavar='NAME',
        help='Only federated remotes (repeatable). Skips the shell.',
    )
    parser.add_argument(
        '--skip-nginx-reload',
        action='store_true',
        help='Do not rewrite nginx site conf or reload after the build.',
    )
    args = parser.parse_args(argv)
    only = [name.strip() for name in args.module if name and name.strip()]
    environ = _merged_env(PROJECT_ROOT)
    return execute_client_build(
        PROJECT_ROOT,
        environ,
        only_modules=only or None,
        skip_nginx_reload=args.skip_nginx_reload,
    )


if __name__ == '__main__':
    raise SystemExit(main())
