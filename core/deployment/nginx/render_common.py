"""
Общие хелперы рендера nginx (host и Docker Compose).

Host и Docker используют одни и те же фрагменты upstream/proxy с разными
целями upstream (127.0.0.1 vs имена сервисов compose).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlparse

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from ergo_modes import env_bool
from security.csp_policy import (
    build_security_headers_nginx,
    read_federation_importmap_hashes,
    resolve_csp_mode,
)
from upload_limits import compute_client_max_body_bytes, format_nginx_body_size
from upload_rate import build_rate_limit_conf, resolve_upload_rates, upload_location_limit_lines


def use_https(values: Mapping[str, str], listen_port: str = '') -> bool:
    if env_bool(values.get('NGINX_USE_HTTPS', '')):
        return True
    port = (listen_port or values.get('NGINX_LISTEN_PORT') or '').strip()
    return port == '443'


def _env(values: Mapping[str, str], key: str, default: str = '') -> str:
    return (values.get(key) or default).strip() or default


def resolve_client_max_body_size(values: Mapping[str, str]) -> str:
    """nginx client_max_body_size из MEDIA_UPLOAD_MAX_SIZE + direct-upload env."""
    return format_nginx_body_size(compute_client_max_body_bytes(values))


def resolve_host_api_upstream(values: Mapping[str, str]) -> str:
    """host:port для ``upstream ergo_api``.

    Пусто — локальный Django ``127.0.0.1:API_PORT``.
    ``NGINX_API_UPSTREAM`` — ядро на другом хосте; location ``/api/<module>/``
    этот ключ не меняет.
    """
    default_port = _env(values, 'API_PORT', '8000')
    raw = _env(values, 'NGINX_API_UPSTREAM')
    if not raw:
        return f'127.0.0.1:{default_port}'
    return _parse_upstream_host_port(raw, default_port)


def _parse_upstream_host_port(raw: str, default_port: str) -> str:
    text = raw.strip()
    if '://' in text:
        parsed = urlparse(text)
        host = (parsed.hostname or '').strip() or '127.0.0.1'
        port = parsed.port or int(default_port)
        return f'{host}:{port}'
    if text.count(':') == 1:
        host, _, port = text.partition(':')
        host = host.strip() or '127.0.0.1'
        port = port.strip() or default_port
        return f'{host}:{port}'
    return f'{text}:{default_port}'


def render_upstream_block(
    name: str,
    server: str,
    *,
    keepalive: int | None = None,
    no_keepalive_comment: bool = False,
) -> str:
    lines = [f'upstream {name} {{']
    if no_keepalive_comment:
        lines.extend([
            '    # No upstream keepalive: on Windows, nginx->Daphne idle keepalive',
            '    # pools cause intermittent /api hangs (infinite SPA boot loader).',
        ])
    lines.append(f'    server {server};')
    if keepalive is not None:
        lines.append(f'    keepalive {keepalive};')
    lines.append('}')
    return '\n'.join(lines)


def resolve_host_media_upstream(values: Mapping[str, str]) -> str:
    """Местный media_api для ``/upload/`` и ``/serve/`` ядра (аватары, вложения)."""
    media_port = _env(values, 'MEDIA_API_BIND_PORT', '8003')
    return f'127.0.0.1:{media_port}'


def resolve_host_media_modules_upstream(values: Mapping[str, str]) -> str | None:
    """nginx пира для ``/upload/<module>/`` и ``/serve/<module>/``.

    ``NGINX_MEDIA_UPSTREAM`` не подменяет местный ``ergo_media``: файлы ядра
    остаются здесь. Без порта — 80 (nginx пира), не bind-порт media_api.
    """
    raw = _env(values, 'NGINX_MEDIA_UPSTREAM')
    if not raw:
        return None
    return _parse_upstream_host_port(raw, '80')


def resolve_host_client_upstream(values: Mapping[str, str]) -> str | None:
    """host:port SPA на другом хосте. Пусто — локальный ``core/client/dist``."""
    raw = _env(values, 'NGINX_CLIENT_UPSTREAM')
    if not raw:
        return None
    return _parse_upstream_host_port(raw, '80')


def resolve_host_client_remotes_upstream(values: Mapping[str, str]) -> str | None:
    """host:port federated remotes. Пусто — местный ``virtual_env/client-remotes``."""
    raw = _env(values, 'NGINX_CLIENT_REMOTES_UPSTREAM')
    if not raw:
        return None
    return _parse_upstream_host_port(raw, '80')


def render_client_upstream_block(values: Mapping[str, str]) -> str:
    parts: list[str] = []
    peer = resolve_host_client_upstream(values)
    if peer:
        parts.append(render_upstream_block('ergo_client', peer, no_keepalive_comment=True))
    remotes_peer = resolve_host_client_remotes_upstream(values)
    if remotes_peer:
        parts.append(
            render_upstream_block('ergo_client_remotes', remotes_peer, no_keepalive_comment=True),
        )
    if not parts:
        return ''
    return '\n'.join(parts) + '\n'


def _remotes_proxy_headers(host: str) -> str:
    return (
        '        proxy_pass http://ergo_client_remotes;\n'
        f'        proxy_set_header Host {host};\n'
        '        proxy_set_header X-Forwarded-Host $host;\n'
        '        proxy_set_header X-Forwarded-Proto $scheme;\n'
        '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
    )


def render_remotes_location_host(values: Mapping[str, str]) -> str:
    """``/remotes/<name>/``: hashed chunks кэшируются, remoteEntry.js — нет."""
    remotes_peer = resolve_host_client_remotes_upstream(values)
    if remotes_peer:
        host = remotes_peer.rsplit(':', 1)[0]
        proxy = _remotes_proxy_headers(host)
        return (
            '    location ^~ /remotes/ {\n'
            f'{proxy}'
            '        location ~* /(?:chunks|assets)/ {\n'
            '            expires 1y;\n'
            '            proxy_hide_header Cache-Control;\n'
            '            add_header Cache-Control "public, immutable" always;\n'
            f'{proxy}'
            '        }\n'
            '        location ~* "\\.[a-fA-F0-9]{8,}\\.(?:js|css)$" {\n'
            '            expires 1y;\n'
            '            proxy_hide_header Cache-Control;\n'
            '            add_header Cache-Control "public, immutable" always;\n'
            f'{proxy}'
            '        }\n'
            '        add_header Cache-Control "no-store" always;\n'
            '    }\n'
            '\n'
        )
    return (
        '    location ^~ /remotes/ {\n'
        '        alias ${ERGO_ROOT}/virtual_env/client-remotes/;\n'
        '        location ~* /(?:chunks|assets)/ {\n'
        '            expires 1y;\n'
        '            add_header Cache-Control "public, immutable" always;\n'
        '            include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
        '        }\n'
        '        location ~* "\\.[a-fA-F0-9]{8,}\\.(?:js|css)$" {\n'
        '            expires 1y;\n'
        '            add_header Cache-Control "public, immutable" always;\n'
        '            include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
        '        }\n'
        '        add_header Cache-Control "no-store" always;\n'
        '        include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
        '    }\n'
        '\n'
    )


def render_spa_locations_host(values: Mapping[str, str]) -> str:
    """``/`` ``/assets/`` ``/index.html``: локальный dist или proxy на NGINX_CLIENT_UPSTREAM."""
    remotes = render_remotes_location_host(values)
    peer = resolve_host_client_upstream(values)
    if not peer:
        return (
            remotes
            + '    location /assets/ {\n'
            '        expires 1y;\n'
            '        add_header Cache-Control "public, immutable" always;\n'
            '        try_files $uri =404;\n'
            '    }\n'
            '\n'
            '    location = /client-build.json {\n'
            '        add_header Cache-Control "no-store" always;\n'
            '        include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
            '        try_files $uri =404;\n'
            '    }\n'
            '\n'
            '    location = /index.html {\n'
            '        if ($maintenance = 1) { return 503; }\n'
            '        add_header Cache-Control "no-store" always;\n'
            '        include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
            '    }\n'
            '\n'
            '    location = / {\n'
            '        if ($maintenance = 1) { return 503; }\n'
            '        add_header Cache-Control "no-store" always;\n'
            '        include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
            '        try_files /index.html =404;\n'
            '    }\n'
            '\n'
            '    location / {\n'
            '        if ($maintenance = 1) { return 503; }\n'
            '        limit_conn ergo_conn 50;\n'
            '        try_files $uri $uri/ /index.html;\n'
            '    }\n'
        )
    host = peer.rsplit(':', 1)[0]
    proxy = (
        f'        proxy_pass http://ergo_client;\n'
        f'        proxy_set_header Host {host};\n'
        '        proxy_set_header X-Forwarded-Host $host;\n'
        '        proxy_set_header X-Forwarded-Proto $scheme;\n'
        '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
    )
    return (
        remotes
        + '    location /assets/ {\n'
        '        expires 1y;\n'
        '        add_header Cache-Control "public, immutable" always;\n'
        f'{proxy}'
        '    }\n'
        '\n'
        '    location = /client-build.json {\n'
        '        add_header Cache-Control "no-store" always;\n'
        '        include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
        f'{proxy}'
        '    }\n'
        '\n'
        '    location = /index.html {\n'
        '        if ($maintenance = 1) { return 503; }\n'
        '        add_header Cache-Control "no-store" always;\n'
        '        include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
        f'{proxy}'
        '    }\n'
        '\n'
        '    location = / {\n'
        '        if ($maintenance = 1) { return 503; }\n'
        '        add_header Cache-Control "no-store" always;\n'
        '        include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
        f'{proxy}'
        '    }\n'
        '\n'
        '    location / {\n'
        '        if ($maintenance = 1) { return 503; }\n'
        '        limit_conn ergo_conn 50;\n'
        f'{proxy}'
        '    }\n'
    )


def build_host_upstream_blocks(values: Mapping[str, str]) -> tuple[str, str]:
    from module_nginx import media_route_modules

    api = render_upstream_block(
        'ergo_api',
        resolve_host_api_upstream(values),
        no_keepalive_comment=True,
    )
    media = render_upstream_block('ergo_media', resolve_host_media_upstream(values))
    peer = resolve_host_media_modules_upstream(values)
    if peer and media_route_modules(values):
        media = media + '\n\n' + render_upstream_block('ergo_media_modules', peer)
    return api, media


def build_docker_upstream_blocks(values: Mapping[str, str]) -> tuple[str, str]:
    api_svc = _env(values, 'DOCKER_SERVICE_API', 'api')
    media_svc = _env(values, 'DOCKER_SERVICE_MEDIA', 'media-api')
    api_port = _env(values, 'API_PORT', '8000')
    media_port = _env(values, 'MEDIA_API_BIND_PORT', '8003')
    api = render_upstream_block('ergo_api', f'{api_svc}:{api_port}', keepalive=32)
    media = render_upstream_block('ergo_media', f'{media_svc}:{media_port}', keepalive=8)
    return api, media


def build_realtime_stream_location(*, variant: Literal['host', 'docker'] = 'host') -> str:
    # SSE: realtime + */stream/ процессов на ergo_api (монолит / ядро).
    # В microservice location ^~ /api/<module>/ забирает stream у модуля:
    # regex иначе бьёт в Django ядра (404). Без имён модулей в этом блоке.
    # Docker без maintenance-map; host — с проверкой $maintenance.
    if variant == 'docker':
        return """    location ^~ /api/realtime/stream/ {
        limit_conn ergo_conn 20;
        limit_conn_status 429;
        proxy_pass http://ergo_api;
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        chunked_transfer_encoding on;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location ~ ^/api/.+/stream/?$ {
        limit_conn ergo_conn 20;
        limit_conn_status 429;
        proxy_pass http://ergo_api;
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        chunked_transfer_encoding on;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
"""
    return """    location ^~ /api/realtime/stream/ {
        if ($maintenance = 1) { return 503; }
        limit_conn ergo_conn 20;
        limit_conn_status 429;
        proxy_pass http://ergo_api;
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        chunked_transfer_encoding on;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location ~ ^/api/.+/stream/?$ {
        if ($maintenance = 1) { return 503; }
        limit_conn ergo_conn 20;
        limit_conn_status 429;
        proxy_pass http://ergo_api;
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        chunked_transfer_encoding on;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
"""


def build_logout_location(*, include_maintenance: bool) -> str:
    """Идемпотентный logout: в API только burst, остальное — 204 без прокси.

    limit_req_status 429, не default 503: иначе server-level error_page
    maintenance превращает POST в GET (405). Сами 429 с этой location
    отвечаем 204: повторный выход не ошибка и не повод ретраить.
    """
    maintenance = (
        '        if ($maintenance = 1) { return 503; }\n'
        if include_maintenance
        else ''
    )
    return (
        '    # Точный logout раньше общего /api/: шторм не бьёт в Daphne.\n'
        '    location = /api/cms/adp/logout/ {\n'
        f'{maintenance}'
        '        limit_req zone=ergo_logout burst=5 nodelay;\n'
        '        limit_req_status 429;\n'
        '        limit_req_log_level notice;\n'
        '        limit_conn ergo_conn 10;\n'
        '        limit_conn_status 429;\n'
        '        error_page 429 =204 @logout_limited;\n'
        '        proxy_pass http://ergo_api;\n'
        '    }\n'
        '\n'
        '    location @logout_limited {\n'
        '        internal;\n'
        '        access_log off;\n'
        '        return 204;\n'
        '    }\n'
    )


def build_host_api_ws_locations() -> str:
    return """    # Служебный ModuleBridge не через публичный reverse proxy (С12).
    location /internal/ {
        deny all;
        access_log off;
    }

""" + build_logout_location(include_maintenance=True) + """
    # Только auth_request из location Jupyter; прямой браузерный GET — 404.
    location = /api/internal/jupyter-access/ {
        internal;
        proxy_pass http://ergo_api;
    }

    location /api/ {
        if ($maintenance = 1) { return 503; }
        limit_req zone=ergo_api burst=50 nodelay;
        limit_req_status 429;
        limit_conn ergo_conn 50;
        limit_conn_status 429;
        proxy_pass http://ergo_api;
    }

    # --- WebSocket: мессенджер, уведомления (/ws/messenger/, /ws/notifications/) ---
    location /ws/ {
        if ($maintenance = 1) { return 503; }
        limit_conn ergo_conn 20;
        limit_conn_status 429;
        proxy_pass http://ergo_api;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
"""


def build_host_media_locations() -> str:
    return """    location /serve/ {
        if ($maintenance = 1) { return 503; }
        limit_req zone=ergo_serve burst=40 nodelay;
        limit_req_status 429;
        limit_conn ergo_conn 30;
        limit_conn_status 429;
        proxy_pass http://ergo_media;
    }

    location /upload/ {
        if ($maintenance = 1) { return 503; }
${ERGO_UPLOAD_LIMIT_LINES}        proxy_pass http://ergo_media;
        client_max_body_size ${ERGO_CLIENT_MAX_BODY_SIZE};
    }

    location /health/ {
        allow 127.0.0.1;
        allow ::1;
        deny all;
        limit_req zone=ergo_api burst=5 nodelay;
        proxy_pass http://ergo_media;
        access_log off;
    }
"""


def build_docker_core_proxy_locations() -> str:
    """Proxy-локации Docker: rate limit / health как на host HTTP (С6).

    /health/ — deny all через опубликованный порт: allow 127.0.0.1 бесполезен
    (клиент с хоста виден как docker-gateway). Healthcheck compose бьёт в media-api.
    """
    stream = build_realtime_stream_location(variant='docker')
    logout = build_logout_location(include_maintenance=False)
    return f"""{stream}
    location /internal/ {{
        deny all;
        access_log off;
    }}

{logout}
    location = /api/internal/jupyter-access/ {{
        internal;
        proxy_pass http://ergo_api;
    }}

    location /api/ {{
        limit_req zone=ergo_api burst=50 nodelay;
        limit_req_status 429;
        limit_conn ergo_conn 50;
        limit_conn_status 429;
        proxy_pass http://ergo_api;
    }}

    location /ws/ {{
        limit_conn ergo_conn 20;
        limit_conn_status 429;
        proxy_pass http://ergo_api;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }}

    location /serve/ {{
        limit_req zone=ergo_serve burst=40 nodelay;
        limit_req_status 429;
        limit_conn ergo_conn 30;
        limit_conn_status 429;
        proxy_pass http://ergo_media;
    }}

    location /upload/ {{
${{ERGO_UPLOAD_LIMIT_LINES}}        proxy_pass http://ergo_media;
        client_max_body_size ${{ERGO_CLIENT_MAX_BODY_SIZE}};
    }}

    location /health/ {{
        deny all;
        access_log off;
    }}
"""


def build_core_proxy_locations(*, variant: Literal['host', 'docker']) -> str:
    """Общий маркер для host/docker: proxy_pass к ergo_api / ergo_media."""
    if variant == 'docker':
        return build_docker_core_proxy_locations()
    return '\n'.join(
        part.strip()
        for part in (
            build_realtime_stream_location(variant='host'),
            build_host_api_ws_locations(),
            build_host_media_locations(),
        )
        if part.strip()
    )


CORE_PROXY_MARKER = 'proxy_pass http://ergo_api'


def apply_template_replacements(content: str, replacements: Mapping[str, str]) -> str:
    # Два прохода: вложенные плейсхолдеры (body size внутри location-блоков).
    for _ in range(2):
        for needle, value in replacements.items():
            content = content.replace(needle, value)
    return content


def build_host_nginx_shared_replacements(values: Mapping[str, str]) -> dict[str, str]:
    from module_nginx import render_module_media_locations_host

    api_upstream, media_upstream = build_host_upstream_blocks(values)
    body_size = resolve_client_max_body_size(values)
    rates = resolve_upload_rates(values)
    return {
        '${ERGO_API_UPSTREAM_BLOCK}': api_upstream,
        '${ERGO_MEDIA_UPSTREAM_BLOCK}': media_upstream,
        '${ERGO_CLIENT_UPSTREAM_BLOCK}': render_client_upstream_block(values),
        '${ERGO_SPA_LOCATIONS}': render_spa_locations_host(values),
        '${ERGO_REALTIME_STREAM_LOCATION}': build_realtime_stream_location(variant='host'),
        '${ERGO_HOST_API_WS_PROXY}': build_host_api_ws_locations(),
        '${ERGO_HOST_MEDIA_PROXY}': (
            render_module_media_locations_host(values) + build_host_media_locations()
        ),
        '${ERGO_CLIENT_MAX_BODY_SIZE}': body_size,
        '${ERGO_RATE_LIMIT_CONF}': build_rate_limit_conf(values).rstrip('\n'),
        '${ERGO_UPLOAD_LIMIT_LINES}': upload_location_limit_lines(burst=int(rates['burst'])),
    }


def _snippet_text(name: str) -> str:
    path = Path(__file__).resolve().parent / 'snippets' / name
    return path.read_text(encoding='utf-8').strip() + '\n'


def build_docker_http_preamble(values: Mapping[str, str] | None = None) -> str:
    """http-контекст внутри conf.d: hardening, gzip, зоны частоты (как host HTTP)."""
    rate_conf = build_rate_limit_conf(values or {})
    return (
        _snippet_text('http_hardening.conf')
        + _snippet_text('compression.conf')
        + rate_conf
    )


def _project_root_from_nginx_path(path: Path) -> Path | None:
    for parent in Path(path).resolve().parents:
        if (parent / 'core' / 'client').is_dir():
            return parent
    return None


def build_docker_nginx_shared_replacements(
    values: Mapping[str, str],
    *,
    project_root: Path | None = None,
) -> dict[str, str]:
    api_upstream, media_upstream = build_docker_upstream_blocks(values)
    body_size = resolve_client_max_body_size(values)
    csp_mode = resolve_csp_mode(values)
    rates = resolve_upload_rates(values)
    return {
        '${ERGO_DOCKER_HTTP_PREAMBLE}': build_docker_http_preamble(values),
        '${ERGO_DOCKER_SECURITY_HEADERS}': build_security_headers_nginx(
            csp_mode,
            extra_script_hashes=read_federation_importmap_hashes(project_root),
        ),
        '${ERGO_DOCKER_PROXY_PARAMS}': _snippet_text('proxy_params.conf'),
        '${ERGO_API_UPSTREAM_BLOCK}': api_upstream,
        '${ERGO_MEDIA_UPSTREAM_BLOCK}': media_upstream,
        '${ERGO_CORE_PROXY_LOCATIONS}': build_core_proxy_locations(variant='docker'),
        '${ERGO_CLIENT_MAX_BODY_SIZE}': body_size,
        '${ERGO_UPLOAD_LIMIT_LINES}': upload_location_limit_lines(burst=int(rates['burst'])),
        '${NGINX_LISTEN_PORT}': _env(values, 'NGINX_LISTEN_PORT', '80'),
        '${NGINX_SERVER_NAME}': _env(values, 'NGINX_SERVER_NAME', 'localhost'),
        '${API_JUPYTER_BIND_PORT}': _env(values, 'API_JUPYTER_BIND_PORT', '8002'),
    }


def render_docker_nginx_config(
    raw_env: Mapping[str, str],
    *,
    template_path: Path,
    output_path: Path,
) -> Path:
    from module_nginx import (
        render_module_locations_docker,
        render_module_media_locations_docker,
        render_module_upstreams_docker,
    )

    content = template_path.read_text(encoding='utf-8')
    replacements = build_docker_nginx_shared_replacements(
        raw_env,
        project_root=_project_root_from_nginx_path(template_path),
    )
    replacements['${ERGO_MODULE_UPSTREAMS}'] = render_module_upstreams_docker(raw_env)
    replacements['${ERGO_MODULE_LOCATIONS}'] = (
        render_module_media_locations_docker(raw_env) + render_module_locations_docker(raw_env)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = apply_template_replacements(content, replacements).replace('\r\n', '\n')
    output_path.write_bytes(rendered.encode('utf-8'))
    return output_path
