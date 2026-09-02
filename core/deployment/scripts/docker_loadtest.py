"""
ergoms docker-loadtest-up / docker-loadtest-down

Поднятие profile loadtest (postgres-loadtest + api-loadtest) и migrate.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
_DOCKER_DIR = _DEPLOYMENT_DIR / 'docker'
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from env_resolvers import load_merged_env  # noqa: E402
from lifecycle.docker import ops as docker_ops  # noqa: E402


def _with_loadtest_profile(root: Path) -> dict[str, str]:
    raw = dict(load_merged_env(root))
    raw['DOCKER_PROFILE_LOADTEST'] = 'true'
    return raw


def cmd_up(_: argparse.Namespace) -> int:
    root = _PROJECT_ROOT.resolve()
    os.environ['DOCKER_PROFILE_LOADTEST'] = 'true'
    raw = _with_loadtest_profile(root)
    env = {**os.environ, **{k: str(v) for k, v in raw.items()}}
    cmd, cwd = docker_ops.build_compose_cmd(
        'up',
        extra_args=['-d', 'redis', 'postgres-loadtest', 'api-loadtest'],
        project_root=root,
    )
    print(format_console('info', t('docker_loadtest_up_start')))
    code = subprocess.call(cmd, cwd=str(cwd), env=env)
    if code != 0:
        print(format_console('error', t('docker_loadtest_up_failed', code=code)), file=sys.stderr)
        return code

    print(format_console('info', t('docker_loadtest_migrate')))
    migrate_cmd, migrate_cwd = docker_ops.build_compose_cmd(
        'exec',
        extra_args=[
            '-T',
            'api-loadtest',
            'bash',
            '-o',
            'pipefail',
            '-c',
            docker_ops.api_migrate_shell(),
        ],
        project_root=root,
    )
    mig_code = subprocess.call(migrate_cmd, cwd=str(migrate_cwd), env=env)
    if mig_code != 0:
        print(
            format_console('warning', t('docker_loadtest_migrate_warn', code=mig_code)),
            file=sys.stderr,
        )
    else:
        print(format_console('ok', t('docker_loadtest_up_ok')))
    return 0


def cmd_down(_: argparse.Namespace) -> int:
    root = _PROJECT_ROOT.resolve()
    os.environ['DOCKER_PROFILE_LOADTEST'] = 'true'
    env = {**os.environ, **{k: str(v) for k, v in _with_loadtest_profile(root).items()}}
    cmd, cwd = docker_ops.build_compose_cmd(
        'stop',
        extra_args=['api-loadtest', 'postgres-loadtest'],
        project_root=root,
    )
    print(format_console('info', t('docker_loadtest_down_start')))
    code = subprocess.call(cmd, cwd=str(cwd), env=env)
    if code != 0:
        print(format_console('error', t('docker_loadtest_down_failed', code=code)), file=sys.stderr)
        return code
    print(format_console('ok', t('docker_loadtest_down_ok')))
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description='ErgoMS Docker loadtest stack')
    sub = parser.add_subparsers(dest='command', required=True)
    up_p = sub.add_parser('up')
    up_p.set_defaults(handler=cmd_up)
    down_p = sub.add_parser('down')
    down_p.set_defaults(handler=cmd_down)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == '__main__':
    raise SystemExit(main())
