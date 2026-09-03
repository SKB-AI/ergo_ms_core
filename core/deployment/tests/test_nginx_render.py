from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _bootstrap  # noqa: F401

import render_nginx_config  # noqa: E402
from lifecycle.docker.ops import render_nginx_docker_config  # noqa: E402
from render_common import (  # noqa: E402
    CORE_PROXY_MARKER,
    build_core_proxy_locations,
    build_docker_upstream_blocks,
    build_host_upstream_blocks,
    render_docker_nginx_config,
    render_spa_locations_host,
    resolve_host_api_upstream,
    resolve_host_client_remotes_upstream,
    resolve_host_client_upstream,
    resolve_host_media_modules_upstream,
    resolve_host_media_upstream,
)


class NginxRenderTests(unittest.TestCase):
    def test_shared_core_proxy_locations_for_host_and_docker(self) -> None:
        host_block = build_core_proxy_locations(variant='host')
        docker_block = build_core_proxy_locations(variant='docker')
        self.assertIn(CORE_PROXY_MARKER, host_block)
        self.assertIn(CORE_PROXY_MARKER, docker_block)
        self.assertIn('proxy_pass http://ergo_media', host_block)
        self.assertIn('proxy_pass http://ergo_media', docker_block)
        self.assertIn('location /internal/', host_block)
        self.assertIn('location /internal/', docker_block)
        host_internal = host_block[host_block.find('location /internal/'):]
        docker_internal = docker_block[docker_block.find('location /internal/'):]
        self.assertIn('deny all', host_internal[:160])
        self.assertIn('deny all', docker_internal[:160])
        for block in (host_block, docker_block):
            self.assertIn('location = /api/cms/adp/logout/', block)
            self.assertIn('error_page 429 =204 @logout_limited', block)
            self.assertIn('location @logout_limited', block)

    def test_upstream_targets_differ_host_vs_docker(self) -> None:
        values = {
            'API_PORT': '8000',
            'MEDIA_API_BIND_PORT': '8003',
            'DOCKER_SERVICE_API': 'api',
            'DOCKER_SERVICE_MEDIA': 'media-api',
        }
        host_api, host_media = build_host_upstream_blocks(values)
        docker_api, docker_media = build_docker_upstream_blocks(values)
        self.assertIn('127.0.0.1:8000', host_api)
        self.assertIn('127.0.0.1:8003', host_media)
        self.assertIn('api:8000', docker_api)
        self.assertIn('media-api:8003', docker_media)

    def test_host_api_upstream_can_point_to_remote_core(self) -> None:
        self.assertEqual(
            resolve_host_api_upstream({'API_PORT': '8000'}),
            '127.0.0.1:8000',
        )
        self.assertEqual(
            resolve_host_api_upstream({
                'API_PORT': '8000',
                'NGINX_API_UPSTREAM': '10.0.0.2:8000',
            }),
            '10.0.0.2:8000',
        )
        self.assertEqual(
            resolve_host_api_upstream({
                'API_PORT': '9000',
                'NGINX_API_UPSTREAM': 'http://core.internal:8000',
            }),
            'core.internal:8000',
        )
        host_api, _ = build_host_upstream_blocks({
            'API_PORT': '8000',
            'MEDIA_API_BIND_PORT': '8003',
            'NGINX_API_UPSTREAM': '10.0.0.2:8000',
        })
        self.assertIn('10.0.0.2:8000', host_api)
        self.assertNotIn('127.0.0.1:8000', host_api)

    def test_host_media_upstream_stays_local_when_modules_peer_is_set(self) -> None:
        self.assertEqual(
            resolve_host_media_upstream({'MEDIA_API_BIND_PORT': '8003'}),
            '127.0.0.1:8003',
        )
        self.assertEqual(
            resolve_host_media_upstream({
                'MEDIA_API_BIND_PORT': '8003',
                'NGINX_MEDIA_UPSTREAM': '10.0.0.8:80',
            }),
            '127.0.0.1:8003',
        )
        self.assertEqual(
            resolve_host_media_modules_upstream({
                'NGINX_MEDIA_UPSTREAM': 'http://modules.internal',
            }),
            'modules.internal:80',
        )
        _, host_media = build_host_upstream_blocks({
            'API_PORT': '8000',
            'MEDIA_API_BIND_PORT': '8003',
            'MODULE_RUNTIME': 'microservice',
            'MICROSERVICE_MODULES': 'demo_mod',
            'NGINX_MEDIA_UPSTREAM': '10.0.0.8:80',
        })
        self.assertIn('127.0.0.1:8003', host_media)
        self.assertIn('upstream ergo_media_modules', host_media)
        self.assertIn('10.0.0.8:80', host_media)

    def test_host_client_upstream_proxies_spa_to_remote_host(self) -> None:
        self.assertIsNone(resolve_host_client_upstream({}))
        self.assertEqual(
            resolve_host_client_upstream({'NGINX_CLIENT_UPSTREAM': '10.0.0.8:80'}),
            '10.0.0.8:80',
        )
        self.assertEqual(
            resolve_host_client_upstream({
                'NGINX_CLIENT_UPSTREAM': 'http://modules.internal',
            }),
            'modules.internal:80',
        )
        local = render_spa_locations_host({})
        self.assertIn('try_files $uri $uri/ /index.html;', local)
        self.assertNotIn('proxy_pass http://ergo_client', local)
        self.assertIn('location ^~ /shared/', local)
        self.assertIn('add_header Cache-Control "no-store" always;', local)
        remote = render_spa_locations_host({
            'NGINX_CLIENT_UPSTREAM': '10.0.0.8:80',
        })
        self.assertIn('proxy_pass http://ergo_client;', remote)
        self.assertIn('proxy_set_header Host 10.0.0.8;', remote)
        self.assertIn('location ^~ /shared/', remote)
        self.assertNotIn('try_files $uri $uri/ /index.html;', remote)

        deployment_dir = Path(__file__).resolve().parents[1]
        host_template = deployment_dir / 'nginx' / 'ergo_ms_http.conf.template'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text(
                'NGINX_CLIENT_UPSTREAM=10.0.0.8:80\n',
                encoding='utf-8',
            )
            rendered = render_nginx_config.render_template(
                host_template,
                root=root,
                server_name='core.local',
                listen_host='0.0.0.0',
                listen_port='80',
                use_https=False,
            )
        self.assertIn('upstream ergo_client', rendered)
        self.assertIn('server 10.0.0.8:80;', rendered)
        self.assertIn('proxy_pass http://ergo_client;', rendered)
        self.assertNotIn('${ERGO_SPA_LOCATIONS}', rendered)
        self.assertNotIn('${ERGO_CLIENT_UPSTREAM_BLOCK}', rendered)

    def test_host_client_remotes_location_local_or_proxy(self) -> None:
        self.assertIsNone(resolve_host_client_remotes_upstream({}))
        self.assertEqual(
            resolve_host_client_remotes_upstream({
                'NGINX_CLIENT_REMOTES_UPSTREAM': '10.0.0.8:80',
            }),
            '10.0.0.8:80',
        )
        local = render_spa_locations_host({})
        self.assertIn('location ^~ /remotes/', local)
        self.assertIn('alias ${ERGO_ROOT}/virtual_env/client-remotes/;', local)
        self.assertIn('location ~* /(?:chunks|assets)/', local)
        self.assertIn('public, immutable', local)
        self.assertNotIn('proxy_pass http://ergo_client_remotes', local)
        remote = render_spa_locations_host({
            'NGINX_CLIENT_REMOTES_UPSTREAM': '10.0.0.8:80',
        })
        self.assertIn('proxy_pass http://ergo_client_remotes;', remote)
        self.assertIn('proxy_set_header Host 10.0.0.8;', remote)
        self.assertIn('location ~* /(?:chunks|assets)/', remote)
        self.assertIn('proxy_hide_header Cache-Control', remote)
        self.assertNotIn('alias ${ERGO_ROOT}/virtual_env/client-remotes/;', remote)

    def test_host_and_docker_renderers_use_shared_function(self) -> None:
        deployment_dir = Path(__file__).resolve().parents[1]
        docker_template = deployment_dir / 'docker' / 'nginx' / 'ergo_ms.docker.conf.template'
        host_template = deployment_dir / 'nginx' / 'ergo_ms_http.conf.template'
        raw_env = {
            'DOCKER_SERVICE_API': 'api',
            'DOCKER_SERVICE_MEDIA': 'media-api',
            'API_PORT': '9001',
            'MEDIA_API_BIND_PORT': '9003',
            'NGINX_LISTEN_PORT': '8080',
            'NGINX_SERVER_NAME': 'test.local',
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text(
                'API_PORT=9001\nMEDIA_API_BIND_PORT=9003\n',
                encoding='utf-8',
            )
            out = Path(tmp) / 'docker.conf'
            render_docker_nginx_config(raw_env, template_path=docker_template, output_path=out)
            docker_rendered = out.read_text(encoding='utf-8')

            host_rendered = render_nginx_config.render_template(
                host_template,
                root=root,
                server_name='test.local',
                listen_host='0.0.0.0',
                listen_port='8080',
                use_https=False,
            )

        self.assertIn('api:9001', docker_rendered)
        self.assertIn('127.0.0.1:9001', host_rendered)
        self.assertIn(CORE_PROXY_MARKER, docker_rendered)
        self.assertIn(CORE_PROXY_MARKER, host_rendered)

    def test_ops_wrapper_delegates_to_shared_renderer(self) -> None:
        deployment_dir = Path(__file__).resolve().parents[1]
        docker_template = deployment_dir / 'docker' / 'nginx' / 'ergo_ms.docker.conf.template'
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'ergo_ms.conf.rendered'
            with mock.patch('lifecycle.docker.ops.DOCKER_DIR', Path(tmp)):
                with mock.patch(
                    'render_common.render_docker_nginx_config',
                    wraps=render_docker_nginx_config,
                ) as mocked:
                    (Path(tmp) / 'nginx').mkdir()
                    (Path(tmp) / 'nginx' / 'ergo_ms.docker.conf.template').write_text(
                        docker_template.read_text(encoding='utf-8'),
                        encoding='utf-8',
                    )
                    render_nginx_docker_config({'API_PORT': '8000'})
                    mocked.assert_called_once()

    def test_client_max_body_size_from_upload_limits(self) -> None:
        """tasks 600 MiB + 10% margin → 660m (hard не выше tasks)."""
        deployment_dir = Path(__file__).resolve().parents[1]
        docker_template = deployment_dir / 'docker' / 'nginx' / 'ergo_ms.docker.conf.template'
        host_template = deployment_dir / 'nginx' / 'ergo_ms_http.conf.template'
        raw_env = {
            'DOCKER_SERVICE_API': 'api',
            'DOCKER_SERVICE_MEDIA': 'media-api',
            'API_PORT': '8000',
            'MEDIA_API_BIND_PORT': '8003',
            'MEDIA_UPLOAD_MAX_SIZE': '524288000',
            'MEDIA_UPLOAD_HARD_MAX_SIZE': '524288000',
            'TASKS_MAX_ATTACHMENT_SIZE_MB': '600',
            'CLIENT_VIDEO_UPLOAD_MAX_SIZE_MB': '100',
            'NGINX_UPLOAD_BODY_MARGIN_PERCENT': '10',
            'NGINX_LISTEN_PORT': '80',
            'NGINX_SERVER_NAME': 'localhost',
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text(
                'MEDIA_UPLOAD_MAX_SIZE=524288000\n'
                'MEDIA_UPLOAD_HARD_MAX_SIZE=524288000\n'
                'TASKS_MAX_ATTACHMENT_SIZE_MB=600\n'
                'CLIENT_VIDEO_UPLOAD_MAX_SIZE_MB=100\n',
                encoding='utf-8',
            )
            out = Path(tmp) / 'docker.conf'
            render_docker_nginx_config(raw_env, template_path=docker_template, output_path=out)
            docker_rendered = out.read_text(encoding='utf-8')
            host_rendered = render_nginx_config.render_template(
                host_template,
                root=root,
                server_name='localhost',
                listen_host='0.0.0.0',
                listen_port='80',
                use_https=False,
            )

        self.assertIn('client_max_body_size 660m;', docker_rendered)
        self.assertIn('client_max_body_size 660m;', host_rendered)
        self.assertNotIn('610m', docker_rendered)
        self.assertNotIn('610m', host_rendered)
        self.assertNotIn('${ERGO_CLIENT_MAX_BODY_SIZE}', docker_rendered)
        self.assertNotIn('${ERGO_CLIENT_MAX_BODY_SIZE}', host_rendered)

    def test_docker_nginx_parity_with_host_http(self) -> None:
        """С6: headers, rate zones/limits, /health/ deny в Docker-рендере."""
        deployment_dir = Path(__file__).resolve().parents[1]
        docker_template = deployment_dir / 'docker' / 'nginx' / 'ergo_ms.docker.conf.template'
        raw_env = {
            'DOCKER_SERVICE_API': 'api',
            'DOCKER_SERVICE_MEDIA': 'media-api',
            'API_PORT': '8000',
            'MEDIA_API_BIND_PORT': '8003',
            'NGINX_LISTEN_PORT': '80',
            'NGINX_SERVER_NAME': 'localhost',
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'docker.conf'
            render_docker_nginx_config(raw_env, template_path=docker_template, output_path=out)
            rendered = out.read_text(encoding='utf-8')

        self.assertIn('server_tokens off', rendered)
        self.assertIn('limit_req_zone', rendered)
        self.assertIn('X-Frame-Options', rendered)
        self.assertIn('limit_req zone=ergo_api', rendered)
        self.assertIn('limit_req zone=ergo_upload', rendered)
        self.assertNotIn('5r/m', rendered)
        self.assertIn('rate=1000r/m', rendered)
        self.assertIn('limit_req_status 429', rendered)
        self.assertIn('limit_conn_status 429', rendered)
        api_loc = rendered[rendered.find('location /api/'):]
        self.assertIn('limit_req_status 429', api_loc[:400])
        self.assertIn('limit_conn_status 429', api_loc[:400])
        self.assertIn('burst=25', rendered)
        self.assertIn('location /health/', rendered)
        health = rendered[rendered.find('location /health/'):]
        self.assertIn('deny all', health[:240])
        self.assertIn('/api/realtime/stream/', rendered)
        self.assertIn(r'location ~ ^/api/.+/stream/?$', rendered)
        self.assertIn('proxy_buffering off;', rendered)
        self.assertIn('location = /api/internal/jupyter-access/', rendered)
        jupyter_gate = rendered[rendered.find('location = /api/internal/jupyter-access/'):]
        self.assertIn('internal;', jupyter_gate[:200])
        self.assertIn('location /internal/', rendered)
        internal = rendered[rendered.find('location /internal/'):]
        self.assertIn('deny all', internal[:160])


class ModuleNginxTests(unittest.TestCase):
    def test_host_locations_use_named_unavailable(self) -> None:
        from module_nginx import render_module_locations_host

        block = render_module_locations_host({
            'MODULE_RUNTIME': 'microservice',
            'MICROSERVICE_MODULES': 'demo_mod',
            'DEMO_MOD_PORT': '8123',
            'BRIDGE_SERVICE_URLS': 'demo_mod=http://10.1.2.3:8123',
        })
        self.assertIn('location ^~ /api/demo_mod/', block)
        self.assertIn('location ~ /stream/?$', block)
        self.assertIn('proxy_buffering off;', block)
        self.assertIn('error_page 502 503 504 =503 @module_unavailable', block)
        self.assertIn('location @module_unavailable', block)
        self.assertIn('X-Request-ID', block)
        self.assertIn('proxy_set_header Host 10.1.2.3;', block)
        self.assertIn('proxy_set_header X-Forwarded-Host $host;', block)

    def test_upstreams_have_max_fails(self) -> None:
        from module_nginx import render_module_upstreams_host

        block = render_module_upstreams_host({
            'MODULE_RUNTIME': 'microservice',
            'MICROSERVICE_MODULES': 'demo_mod',
            'DEMO_MOD_PORT': '8123',
        })
        self.assertIn('max_fails=3 fail_timeout=10s', block)

    def test_bind_any_module_host_proxies_loopback(self) -> None:
        from module_nginx import render_module_locations_host, render_module_upstreams_host

        values = {
            'MODULE_RUNTIME': 'microservice',
            'MICROSERVICE_MODULES': 'demo_mod',
            'DEMO_MOD_HOST': '0.0.0.0',
            'DEMO_MOD_PORT': '8123',
        }
        upstreams = render_module_upstreams_host(values)
        locations = render_module_locations_host(values)
        self.assertIn('server 127.0.0.1:8123', upstreams)
        self.assertNotIn('0.0.0.0', upstreams)
        self.assertIn('proxy_set_header Host 127.0.0.1;', locations)
        self.assertNotIn('Host 0.0.0.0', locations)

    def test_monolith_module_blocks_empty(self) -> None:
        from module_nginx import render_module_locations_docker, render_module_upstreams_docker

        values = {'MODULE_RUNTIME': 'monolith', 'MICROSERVICE_MODULES': 'demo_mod'}
        self.assertEqual(render_module_upstreams_docker(values), '')
        self.assertEqual(render_module_locations_docker(values), '')

    def test_docker_microservice_upstream_and_location(self) -> None:
        from module_nginx import render_module_locations_docker, render_module_upstreams_docker

        values = {
            'MODULE_RUNTIME': 'microservice',
            'MICROSERVICE_MODULES': 'demo_mod',
            'DEMO_MOD_PORT': '8123',
        }
        upstreams = render_module_upstreams_docker(values)
        locations = render_module_locations_docker(values)
        self.assertIn('upstream ergo_module_demo_mod', upstreams)
        self.assertIn('server demo_mod:8123', upstreams)
        self.assertIn('location ^~ /api/demo_mod/', locations)
        self.assertIn('location ~ /stream/?$', locations)
        self.assertIn('@module_unavailable', locations)

    def test_docker_template_renders_module_proxy(self) -> None:
        deployment_dir = Path(__file__).resolve().parents[1]
        docker_template = deployment_dir / 'docker' / 'nginx' / 'ergo_ms.docker.conf.template'
        raw_env = {
            'DOCKER_SERVICE_API': 'api',
            'DOCKER_SERVICE_MEDIA': 'media-api',
            'API_PORT': '18000',
            'MEDIA_API_BIND_PORT': '8003',
            'NGINX_LISTEN_PORT': '18080',
            'NGINX_SERVER_NAME': 'localhost',
            'MODULE_RUNTIME': 'microservice',
            'MICROSERVICE_MODULES': 'demo_mod',
            'DEMO_MOD_PORT': '8123',
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'docker.conf'
            render_docker_nginx_config(raw_env, template_path=docker_template, output_path=out)
            rendered = out.read_text(encoding='utf-8')
        self.assertIn('listen 18080', rendered)
        self.assertIn('location ^~ /api/demo_mod/', rendered)
        self.assertIn('demo_mod:8123', rendered)
        self.assertIn('location /internal/', rendered)
        self.assertIn('/api/realtime/stream/', rendered)

    def test_module_media_prefixes_stay_local_without_peer(self) -> None:
        from module_nginx import render_module_media_locations_host

        block = render_module_media_locations_host({
            'MODULE_RUNTIME': 'microservice',
            'MICROSERVICE_MODULES': 'demo_mod',
        })
        self.assertIn('location ^~ /upload/demo_mod/', block)
        self.assertIn('location ^~ /serve/demo_mod/', block)
        self.assertIn('proxy_pass http://ergo_media/upload/;', block)
        self.assertIn('proxy_pass http://ergo_media;', block)
        self.assertNotIn('ergo_media_modules', block)

    def test_module_media_prefixes_go_to_peer(self) -> None:
        from module_nginx import render_module_media_locations_host
        from render_common import build_host_nginx_shared_replacements

        values = {
            'API_PORT': '8000',
            'MEDIA_API_BIND_PORT': '8003',
            'MODULE_RUNTIME': 'microservice',
            'MICROSERVICE_MODULES': 'demo_mod',
            'NGINX_MEDIA_UPSTREAM': '10.0.0.8:80',
        }
        block = render_module_media_locations_host(values)
        self.assertIn('proxy_pass http://ergo_media_modules/upload/;', block)
        self.assertIn('proxy_pass http://ergo_media_modules;', block)
        self.assertIn('proxy_set_header Host 10.0.0.8;', block)
        rendered = build_host_nginx_shared_replacements(values)['${ERGO_HOST_MEDIA_PROXY}']
        self.assertIn('location ^~ /upload/demo_mod/', rendered)
        self.assertIn('location /upload/', rendered)
        upload_generic = rendered[rendered.find('    location /upload/'):]
        self.assertIn('proxy_pass http://ergo_media;', upload_generic)

    def test_docker_template_proxies_jupyter(self) -> None:
        deployment_dir = Path(__file__).resolve().parents[1]
        docker_template = deployment_dir / 'docker' / 'nginx' / 'ergo_ms.docker.conf.template'
        raw_env = {
            'DOCKER_SERVICE_API': 'api',
            'DOCKER_SERVICE_MEDIA': 'media-api',
            'API_PORT': '8000',
            'MEDIA_API_BIND_PORT': '8003',
            'NGINX_LISTEN_PORT': '80',
            'NGINX_SERVER_NAME': 'localhost',
            'API_JUPYTER_BIND_PORT': '18002',
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'docker.conf'
            render_docker_nginx_config(raw_env, template_path=docker_template, output_path=out)
            rendered = out.read_text(encoding='utf-8')
        self.assertIn('location /jupyter/', rendered)
        self.assertIn('http://jupyter:18002/jupyter/', rendered)


if __name__ == '__main__':
    unittest.main()
