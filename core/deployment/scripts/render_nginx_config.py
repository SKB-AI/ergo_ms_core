"""
Рендер nginx-шаблонов ERGO MS с подстановкой host policy и прочих переменных.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
DEPLOYMENT_NGINX = DEPLOYMENT_DIR / 'nginx'
sys.path.insert(0, str(DEPLOYMENT_DIR))
sys.path.insert(0, str(DEPLOYMENT_NGINX))

from env_file_loader import load_project_env  # noqa: E402
from host_policy import compute_template_vars  # noqa: E402
from jupyter_nginx import (  # noqa: E402
    render_jupyter_location_block,
    render_jupyter_upstream_block,
)
from module_nginx import (  # noqa: E402
    render_module_locations_host,
    render_module_upstreams_host,
)
from render_common import (  # noqa: E402
    apply_template_replacements,
    build_host_nginx_shared_replacements,
)
from security.csp_policy import (  # noqa: E402
    build_security_headers_nginx,
    read_federation_importmap_hashes,
    resolve_csp_mode,
    substitute_security_headers_includes,
)
from tls_config import webroot_path  # noqa: E402


def render_template(
    template_path: Path,
    *,
    root: Path,
    server_name: str,
    listen_host: str,
    listen_port: str,
    use_https: bool,
    ssl_cert: str = '',
    ssl_key: str = '',
) -> str:
    values = load_project_env(root)
    snippets_dir = root / 'core' / 'deployment' / 'nginx' / 'snippets'
    root_forward = str(root).replace('\\', '/')
    snippets_forward = str(snippets_dir).replace('\\', '/')

    extra = compute_template_vars(
        values,
        listen_host=listen_host,
        listen_port=listen_port,
        use_https=use_https,
    )

    if use_https and not ssl_cert:
        ssl_cert = '/etc/ssl/certs/ssl-cert-snakeoil.pem'
    if use_https and not ssl_key:
        ssl_key = '/etc/ssl/private/ssl-cert-snakeoil.key'

    content = template_path.read_text(encoding='utf-8')
    csp_mode = resolve_csp_mode(values)
    maintenance_snippet_path = DEPLOYMENT_NGINX / 'snippets' / 'maintenance.conf'
    maintenance_snippet = ''
    if maintenance_snippet_path.is_file():
        maintenance_snippet = maintenance_snippet_path.read_text(encoding='utf-8').replace(
            '${ERGO_ROOT}',
            root_forward,
        )
    jupyter_upstream = render_jupyter_upstream_block(values)
    jupyter_location = render_jupyter_location_block(values)
    module_upstreams = render_module_upstreams_host(values)
    module_locations = render_module_locations_host(values)
    tls_webroot = webroot_path(values, root=root).replace('\\', '/')
    replacements = {
        '${ERGO_ROOT}': root_forward,
        '${ERGO_SERVER_NAME}': server_name,
        '${ERGO_LISTEN_HOST}': listen_host,
        '${ERGO_LISTEN_PORT}': listen_port,
        '${ERGO_NGINX_SNIPPETS}': snippets_forward,
        '${ERGO_SSL_CERT}': ssl_cert.replace('\\', '/'),
        '${ERGO_SSL_KEY}': ssl_key.replace('\\', '/'),
        '${ERGO_TLS_WEBROOT}': tls_webroot,
        '${ERGO_HOST_POLICY_BLOCKS}': extra['ERGO_HOST_POLICY_BLOCKS'],
        '${ERGO_HTTP_CANONICAL_REDIRECT}': extra['ERGO_HTTP_CANONICAL_REDIRECT'],
        '${ERGO_MAINTENANCE_SNIPPET}': maintenance_snippet,
        '${ERGO_JUPYTER_UPSTREAM}': jupyter_upstream,
        '${ERGO_JUPYTER_LOCATION}': jupyter_location,
        '${ERGO_MODULE_UPSTREAMS}': module_upstreams,
        '${ERGO_MODULE_LOCATIONS}': module_locations,
    }
    replacements.update(build_host_nginx_shared_replacements(values))

    content = re.sub(
        r'^\$\{ERGO_HOST_POLICY_BLOCKS\}\s*$',
        replacements['${ERGO_HOST_POLICY_BLOCKS}'],
        content,
        flags=re.MULTILINE,
    )
    rendered = apply_template_replacements(content, replacements)
    return substitute_security_headers_includes(
        rendered,
        build_security_headers_nginx(
            csp_mode,
            extra_script_hashes=read_federation_importmap_hashes(root),
        ),
    )


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser()
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--server-name', default='localhost')
    parser.add_argument('--listen-host', default='0.0.0.0')
    parser.add_argument('--listen-port', default='80')
    parser.add_argument('--use-https', choices=('true', 'false'), default='false')
    parser.add_argument('--ssl-cert', default='')
    parser.add_argument('--ssl-key', default='')
    args = parser.parse_args()

    rendered = render_template(
        args.template,
        root=args.root,
        server_name=args.server_name,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        use_https=args.use_https == 'true',
        ssl_cert=args.ssl_cert,
        ssl_key=args.ssl_key,
    )
    sys.stdout.write(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
