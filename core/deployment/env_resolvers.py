"""
Effective значения переменных окружения для deployment-скриптов (stdlib).

Не записывает .env — дублирует логику src.config.nginx_runtime / tls_runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parent
_NGINX_DIR = _DEPLOYMENT_DIR / 'nginx'
if str(_NGINX_DIR) not in sys.path:
    sys.path.insert(0, str(_NGINX_DIR))

from detect_lan_ip import detect_lan_ip  # noqa: E402
from env_file_loader import load_project_env, parse_env_file  # noqa: E402
from ergo_modes import effective_nginx_enabled  # noqa: E402
from host_policy import is_valid_hostname  # noqa: E402
from tls_config import cert_exists, cert_paths, primary_domain  # noqa: E402
from jupyter_nginx import resolve_jupyter_vars  # noqa: E402


def read_env_file(path: Path) -> dict[str, str]:
    """Читает один .env-файл. Для полного merge корня используйте load_merged_env."""
    return parse_env_file(path)


def load_merged_env(root: Path) -> dict[str, str]:
    """Корневой .env + env/*.env."""
    return load_project_env(root)


def _use_https(values: dict[str, str]) -> bool:
    from render_common import use_https

    return use_https(values)


def resolve_nginx_vars(values: dict[str, str]) -> dict[str, str]:
    """Effective nginx/TLS-переменные для install-nginx и render_nginx_config."""
    enabled = effective_nginx_enabled(values)
    use_https = _use_https(values)

    public_host = values.get('NGINX_PUBLIC_HOST', '').strip()
    server_name = values.get('NGINX_SERVER_NAME', 'localhost').strip()

    if not public_host or public_host in ('localhost', '127.0.0.1'):
        if enabled and server_name in ('', 'localhost', '127.0.0.1'):
            detected = detect_lan_ip()
            public_host = detected or server_name or 'localhost'
        else:
            public_host = server_name or 'localhost'

    if public_host and server_name in ('', 'localhost', '127.0.0.1'):
        server_name = public_host

    listen_host = values.get('NGINX_LISTEN_HOST', '').strip()
    if not listen_host:
        listen_host = '127.0.0.1' if use_https else '0.0.0.0'

    listen_port = values.get('NGINX_LISTEN_PORT', '').strip() or '80'

    ssl_cert = values.get('ERGO_SSL_CERT', '').strip()
    ssl_key = values.get('ERGO_SSL_KEY', '').strip()
    if use_https and (not ssl_cert or not ssl_key):
        domain = primary_domain(values) or (
            public_host if is_valid_hostname(public_host) else ''
        )
        if domain and cert_exists(domain):
            ssl_cert, ssl_key = cert_paths(domain)

    resolved: dict[str, str] = {
        'NGINX_PUBLIC_HOST': public_host,
        'NGINX_SERVER_NAME': server_name,
        'NGINX_LISTEN_HOST': listen_host,
        'NGINX_LISTEN_PORT': listen_port,
        # cmd_render/_rewrite_site_conf выбирают шаблон и SSL-переменные по этому
        # полю в РЕЗУЛЬТАТЕ resolve_nginx_vars, а не по исходному values —
        # без него HTTPS-флаг из .env терялся и nginx падал на HTTP-шаблон.
        'NGINX_USE_HTTPS': 'true' if use_https else 'false',
    }
    if ssl_cert:
        resolved['ERGO_SSL_CERT'] = ssl_cert
    if ssl_key:
        resolved['ERGO_SSL_KEY'] = ssl_key
    return resolved


def resolve_jupyter_deployment_vars(values: dict[str, str]) -> dict[str, str]:
    """Effective Jupyter-переменные для render_nginx_config и ergoms resolve_env."""
    return resolve_jupyter_vars(values)
