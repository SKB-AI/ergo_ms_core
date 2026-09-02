"""
Shared nginx status / reload / test for host lifecycle.

Install и NSSM/systemd остаются в shell-адаптерах; здесь — portable nginx CLI.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _DEPLOYMENT_DIR / 'scripts'
for _path in (_DEPLOYMENT_DIR, _SCRIPTS_DIR):
    _path_str = str(_path)
    if _path_str not in sys.path:
        sys.path.insert(0, _path_str)

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from nginx_foreground import _configure_stdio_utf8, is_nginx_running  # noqa: E402

NGINX_WINDOWS_SERVICE = 'ergo_ms_nginx'
NGINX_LINUX_SERVICE = 'ergo_ms_nginx.service'
NGINX_CONF_NAME = 'ergo_ms'


def _nginx_paths(root: Path) -> tuple[Path, Path, Path]:
    nginx_dir = root / 'virtual_env' / 'packages' / 'nginx'
    if os.name == 'nt':
        exe = nginx_dir / 'nginx.exe'
    else:
        exe = nginx_dir / 'sbin' / 'nginx'
    main_conf = nginx_dir / 'conf' / 'nginx.conf'
    return nginx_dir, exe, main_conf


def _nginx_installed(root: Path) -> bool:
    _nginx_dir, exe, main_conf = _nginx_paths(root)
    return exe.is_file() and main_conf.is_file()


def _is_nginx_service_active() -> bool:
    if os.name == 'nt':
        result = subprocess.run(
            ['sc', 'query', NGINX_WINDOWS_SERVICE],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
        if result.returncode != 0:
            return False
        return 'RUNNING' in (result.stdout or '').upper()

    if not shutil_which('systemctl'):
        return False
    active = subprocess.run(
        ['systemctl', 'is-active', '--quiet', NGINX_LINUX_SERVICE],
        check=False,
    )
    return active.returncode == 0


def shutil_which(name: str) -> str | None:
    from shutil import which
    return which(name)


def _main_conf_arg(main_conf: Path) -> str:
    return str(main_conf).replace('\\', '/') if os.name == 'nt' else str(main_conf)


def cmd_test(root: Path) -> int:
    if not _nginx_installed(root):
        print(format_console('error', t('nginx_not_installed')), file=sys.stderr)
        return 1

    nginx_dir, exe, main_conf = _nginx_paths(root)
    main_conf_arg = _main_conf_arg(main_conf)
    result = subprocess.run(
        [str(exe), '-t', '-c', main_conf_arg],
        cwd=str(nginx_dir),
        check=False,
    )
    return result.returncode


def _site_template(root: Path, *, use_https: bool) -> Path:
    nginx_dir = root / 'core' / 'deployment' / 'nginx'
    https_template = nginx_dir / 'ergo_ms.conf.template'
    http_template = nginx_dir / 'ergo_ms_http.conf.template'
    if use_https and https_template.is_file():
        return https_template
    return http_template


def _rewrite_site_conf(root: Path) -> int:
    """Пересобрать ergo_ms.conf из env (NGINX_API_UPSTREAM, NGINX_CLIENT_UPSTREAM, NGINX_MEDIA_UPSTREAM, модули)."""
    from env_file_loader import load_project_env
    from env_resolvers import resolve_nginx_vars
    from render_common import use_https as nginx_use_https

    values = resolve_nginx_vars(load_project_env(root))
    use_https = nginx_use_https(values)
    template = _site_template(root, use_https=use_https)
    nginx_dir, _exe, _main = _nginx_paths(root)
    output = nginx_dir / 'conf' / f'{NGINX_CONF_NAME}.conf'
    return cmd_render(root, template=template, output=output)


def cmd_reload(root: Path) -> int:
    if not _nginx_installed(root):
        print(format_console('error', t('nginx_not_installed_hint')), file=sys.stderr)
        return 1

    rewritten = _rewrite_site_conf(root)
    if rewritten != 0:
        return rewritten

    nginx_dir, exe, main_conf = _nginx_paths(root)
    main_conf_arg = _main_conf_arg(main_conf)

    print(format_console('info', t('checking_config')))
    test = subprocess.run(
        [str(exe), '-t', '-c', main_conf_arg],
        cwd=str(nginx_dir),
        check=False,
    )
    if test.returncode != 0:
        print(format_console('error', t('config_check_failed')), file=sys.stderr)
        return test.returncode

    if os.name != 'nt' and shutil_which('systemctl'):
        sudo = hasattr(os, 'geteuid') and os.geteuid() != 0 and shutil_which('sudo')
        if _is_nginx_service_active():
            print(format_console('info', t('reloading_nginx_service')))
            reload_cmd = ['systemctl', 'reload', NGINX_LINUX_SERVICE]
            if sudo:
                reload_cmd = ['sudo', *reload_cmd]
            subprocess.run(reload_cmd, check=False)
            print(format_console('ok', t('nginx_reloaded')))
            return 0
        print(format_console('info', t('reloading_nginx')))
        start_cmd = ['systemctl', 'start', NGINX_LINUX_SERVICE]
        if sudo:
            start_cmd = ['sudo', *start_cmd]
        started = subprocess.run(start_cmd, check=False)
        if started.returncode == 0:
            print(format_console('ok', t('nginx_reloaded')))
            return 0

    print(format_console('info', t('reloading_nginx')))
    subprocess.run(
        [str(exe), '-s', 'reload', '-c', main_conf_arg],
        cwd=str(nginx_dir),
        check=False,
    )
    print(format_console('ok', t('nginx_reloaded')))
    return 0


def cmd_status(root: Path) -> int:
    _configure_stdio_utf8()
    nginx_dir, exe, _main_conf = _nginx_paths(root)

    if not _nginx_installed(root):
        print(t('nginx_status_not_installed'))
        print(t('expected_path', path=nginx_dir))
        return 0

    print('')
    print(t('nginx_status_heading'))

    if _is_nginx_service_active():
        label = NGINX_WINDOWS_SERVICE if os.name == 'nt' else NGINX_LINUX_SERVICE
        print(f'  Service ({label}): Running')
    elif is_nginx_running(nginx_dir, exe):
        print(t('process_running'))
    else:
        print('  Process: Not running')

    site_conf = nginx_dir / 'conf' / f'{NGINX_CONF_NAME}.conf'
    if site_conf.is_file():
        print(f'  Site config: {site_conf}')
    return 0


def cmd_render(root: Path, *, template: Path, output: Path | None = None) -> int:
    from env_file_loader import load_project_env  # noqa: WPS433
    from env_resolvers import resolve_nginx_vars  # noqa: WPS433
    from render_nginx_config import render_template  # noqa: WPS433

    values = resolve_nginx_vars(load_project_env(root))
    server_name = values.get('NGINX_SERVER_NAME', 'localhost')
    listen_host = values.get('NGINX_LISTEN_HOST', '0.0.0.0')
    listen_port = values.get('NGINX_LISTEN_PORT', '80')
    use_https = str(values.get('NGINX_USE_HTTPS', 'false')).lower() in ('1', 'true', 'yes')
    ssl_cert = values.get('ERGO_SSL_CERT', '')
    ssl_key = values.get('ERGO_SSL_KEY', '')

    rendered = render_template(
        template,
        root=root,
        server_name=server_name,
        listen_host=listen_host,
        listen_port=listen_port,
        use_https=use_https,
        ssl_cert=ssl_cert,
        ssl_key=ssl_key,
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding='utf-8')
    else:
        sys.stdout.write(rendered)
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdio_utf8()
    parser = argparse.ArgumentParser(description='Nginx backend (status/reload/test/render)')
    parser.add_argument('operation', choices=('status', 'reload', 'test', 'render'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[5])
    parser.add_argument('--template', type=Path, default=None)
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if args.operation == 'status':
        return cmd_status(root)
    if args.operation == 'reload':
        return cmd_reload(root)
    if args.operation == 'test':
        return cmd_test(root)
    if args.operation == 'render':
        if not args.template:
            print(format_console('error', t('render_needs_template')), file=sys.stderr)
            return 1
        return cmd_render(root, template=args.template, output=args.output)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
