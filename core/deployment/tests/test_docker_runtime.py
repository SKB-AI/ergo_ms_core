from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from docker_runtime import (  # noqa: E402
    INFRA_PUBLISH_BIND,
    build_compose_env_overrides,
    compose_profiles,
    build_publish_compose_content,
    build_redis_auth_compose_content,
    docker_home,
    effective_db_host,
    effective_redis_compose_host,
    prepare_compose_artifacts,
    resolve_docker_node_base,
    resolve_docker_python_base,
    resolve_infra_publish_ports,
    write_redis_auth_compose,
)


class DockerRuntimeTests(unittest.TestCase):
    def test_docker_home_uses_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            docker_dir = root / 'core' / 'deployment' / 'docker'
            docker_dir.mkdir(parents=True)
            self.assertEqual(docker_home(root), docker_dir.resolve())

    def test_effective_db_host_container_mode_localhost(self) -> None:
        raw = {'DOCKER_DATABASE': 'container', 'DOCKER_SERVICE_POSTGRES': 'postgres'}
        self.assertEqual(effective_db_host(raw, 'localhost'), 'postgres')
        self.assertEqual(effective_db_host(raw, '127.0.0.1'), 'postgres')

    def test_effective_db_host_external_host_unchanged(self) -> None:
        raw = {'DOCKER_DATABASE': 'container', 'DOCKER_SERVICE_POSTGRES': 'postgres'}
        self.assertEqual(effective_db_host(raw, 'db.example.com'), 'db.example.com')

    def test_effective_db_host_host_mode(self) -> None:
        raw = {'DOCKER_DATABASE': 'host'}
        self.assertEqual(effective_db_host(raw, 'localhost'), 'localhost')

    def test_effective_redis_compose_host(self) -> None:
        raw = {'DOCKER_SERVICE_REDIS': 'redis'}
        self.assertEqual(effective_redis_compose_host(raw, 'localhost'), 'redis')
        self.assertEqual(effective_redis_compose_host(raw, 'redis.example'), 'redis.example')

    def test_build_compose_env_overrides_nginx_and_runtime(self) -> None:
        raw = {
            'ERGO_PROXY': 'nginx',
            'API_PORT': '8000',
            'DOCKER_MODE': 'prod',
        }
        overrides = build_compose_env_overrides(raw)
        self.assertEqual(overrides['ERGO_RUNTIME'], 'docker')
        self.assertEqual(overrides['DOCKER_ENABLED'], 'true')
        self.assertEqual(overrides['NGINX_ENABLED'], 'true')
        self.assertEqual(overrides['CLIENT_USE_RELATIVE_API'], 'true')
        self.assertEqual(overrides['ERGO_ENV'], 'production')
        self.assertEqual(overrides['API_HOST'], '0.0.0.0')
        self.assertEqual(overrides['ERGO_DOCKER_DB_PORT'], '5432')

    def test_build_compose_env_overrides_passes_celery_balance(self) -> None:
        overrides = build_compose_env_overrides(
            {
                'CELERY_BALANCE': 'auto',
                'CELERY_BALANCE_GPU': 'off',
                'CELERY_BALANCE_MIN_CONCURRENCY': '2',
            },
        )
        self.assertEqual(overrides['CELERY_BALANCE'], 'auto')
        self.assertEqual(overrides['CELERY_BALANCE_GPU'], 'off')
        self.assertEqual(overrides['CELERY_BALANCE_MIN_CONCURRENCY'], '2')
        self.assertNotIn('CELERY_BALANCE_MAX_CONCURRENCY', overrides)

    def test_jupyter_profile_sets_container_bind_host(self) -> None:
        overrides = build_compose_env_overrides(
            {'ERGO_JUPYTER': 'local', 'DOCKER_PROFILE_JUPYTER': 'true'},
        )
        self.assertEqual(overrides['API_JUPYTER_BIND_HOST'], '0.0.0.0')

    def test_prepare_compose_artifacts_writes_compose_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text(
                'API_PORT=8000\nERGO_PROXY=nginx\nDOCKER_DATABASE=container\n',
                encoding='utf-8',
            )
            (root / 'databases.yaml').write_text(
                'databases:\n  default:\n    engine: postgresql\n'
                '    host: localhost\n    port: 5432\n    name: ergo_ms\n'
                '    user: postgres\n    password: admin\n',
                encoding='utf-8',
            )
            docker_dir = root / 'core' / 'deployment' / 'docker'
            docker_dir.mkdir(parents=True)
            compose_env = docker_dir / '.compose.env'

            with patch('docker_runtime._DOCKER_DIR', docker_dir), patch(
                'docker_runtime.BUILD_CACHE_OUTPUT',
                docker_dir / 'docker-compose.build.generated.yml',
            ), patch('docker_runtime.load_merged_env') as load_env, patch(
                'docker_runtime.resolve_infra_publish_ports',
                return_value={},
            ), patch(
                'docker_runtime.resolve_docker_app_port',
                side_effect=lambda preferred, env_key='', warn=False: preferred,
            ), patch(
                'lifecycle.docker.ignore.sync_dockerfile_dockerignore',
            ), patch(
                'lifecycle.modules.catalog.ModuleCatalog.from_env',
                return_value=object(),
            ), patch(
                'docker_runtime._docker_image_exists',
                return_value=False,
            ):
                load_env.return_value = {
                    'API_PORT': '8000',
                    'ERGO_PROXY': 'nginx',
                    'DOCKER_DATABASE': 'container',
                }
                result = prepare_compose_artifacts(root)

            self.assertEqual(result['compose_env'], compose_env)
            content = compose_env.read_text(encoding='utf-8')
            self.assertIn('ERGO_RUNTIME=docker', content)
            self.assertIn('DOCKER_ENABLED=true', content)
            self.assertIn('DOCKER_NODE_BASE=node:20-bookworm-slim', content)

    def test_prepare_compose_artifacts_loadtest_does_not_kill_app_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('API_PORT=8000\nDOCKER_DATABASE=container\n', encoding='utf-8')
            (root / 'databases.yaml').write_text(
                'databases:\n  default:\n    engine: postgresql\n'
                '    host: localhost\n    port: 5432\n    name: ergo_ms\n'
                '    user: postgres\n    password: admin\n',
                encoding='utf-8',
            )
            docker_dir = root / 'core' / 'deployment' / 'docker'
            docker_dir.mkdir(parents=True)

            with patch.dict(os.environ, {'DOCKER_PROFILE_LOADTEST': 'true'}), patch(
                'docker_runtime._DOCKER_DIR', docker_dir
            ), patch(
                'docker_runtime.BUILD_CACHE_OUTPUT',
                docker_dir / 'docker-compose.build.generated.yml',
            ), patch('docker_runtime.load_merged_env') as load_env, patch(
                'docker_runtime.resolve_infra_publish_ports',
                return_value={},
            ), patch(
                'docker_runtime.resolve_docker_app_port',
            ) as resolve_port, patch(
                'lifecycle.docker.ignore.sync_dockerfile_dockerignore',
            ), patch(
                'lifecycle.modules.catalog.ModuleCatalog.from_env',
                return_value=object(),
            ), patch(
                'docker_runtime._docker_image_exists',
                return_value=False,
            ):
                load_env.return_value = {
                    'API_PORT': '8000',
                    'DOCKER_DATABASE': 'container',
                    'DOCKER_PROFILE_LOADTEST': 'true',
                }
                prepare_compose_artifacts(root)

            resolve_port.assert_not_called()

    def test_prepare_compose_artifacts_can_skip_app_port_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('API_PORT=8000\nDOCKER_DATABASE=container\n', encoding='utf-8')
            (root / 'databases.yaml').write_text(
                'databases:\n  default:\n    engine: postgresql\n'
                '    host: localhost\n    port: 5432\n    name: ergo_ms\n'
                '    user: postgres\n    password: admin\n',
                encoding='utf-8',
            )
            docker_dir = root / 'core' / 'deployment' / 'docker'
            docker_dir.mkdir(parents=True)

            with patch('docker_runtime._DOCKER_DIR', docker_dir), patch(
                'docker_runtime.BUILD_CACHE_OUTPUT',
                docker_dir / 'docker-compose.build.generated.yml',
            ), patch('docker_runtime.load_merged_env') as load_env, patch(
                'docker_runtime.resolve_infra_publish_ports',
                return_value={},
            ), patch(
                'docker_runtime.resolve_docker_app_port',
            ) as resolve_port, patch(
                'lifecycle.docker.ignore.sync_dockerfile_dockerignore',
            ), patch(
                'lifecycle.modules.catalog.ModuleCatalog.from_env',
                return_value=object(),
            ), patch(
                'docker_runtime._docker_image_exists',
                return_value=False,
            ):
                load_env.return_value = {
                    'API_PORT': '8000',
                    'DOCKER_DATABASE': 'container',
                }
                prepare_compose_artifacts(root, resolve_app_ports=False)

            resolve_port.assert_not_called()

    def test_python_base_prefers_official_slim(self) -> None:
        with patch('docker_runtime._docker_image_exists', return_value=False):
            self.assertEqual(resolve_docker_python_base({}), 'python:3.12-slim-bookworm')

    def test_python_base_explicit_wins(self) -> None:
        self.assertEqual(
            resolve_docker_python_base({'DOCKER_PYTHON_BASE': 'python:3.12-slim'}),
            'python:3.12-slim',
        )

    def test_node_base_uses_official_when_present(self) -> None:
        def exists(name: str) -> bool:
            return name == 'node:20-bookworm-slim'

        with patch('docker_runtime._docker_image_exists', side_effect=exists):
            self.assertEqual(resolve_docker_node_base({}), 'node:20-bookworm-slim')

    def test_node_base_fallback_only_without_official(self) -> None:
        def exists(name: str) -> bool:
            return name == 'ergo_ms-client:local'

        with patch('docker_runtime._docker_image_exists', side_effect=exists):
            self.assertEqual(resolve_docker_node_base({}), 'ergo_ms-client:local')


class RedisPublishAndAuthTests(unittest.TestCase):
    def test_empty_redis_publish_port_skips(self) -> None:
        published = resolve_infra_publish_ports(
            {
                'DOCKER_REDIS_PUBLISH_PORT': '',
                'DOCKER_DATABASE': 'host',
            },
            warn=False,
        )
        self.assertNotIn('redis', published)

    def test_explicit_redis_publish_port(self) -> None:
        published = resolve_infra_publish_ports(
            {
                'DOCKER_REDIS_PUBLISH_PORT': '16379',
                'DOCKER_SERVICE_REDIS': 'redis',
                'DOCKER_DATABASE': 'host',
            },
            warn=False,
        )
        self.assertEqual(published.get('redis'), 16379)

    def test_redis_publish_none_skips(self) -> None:
        published = resolve_infra_publish_ports(
            {
                'DOCKER_REDIS_PUBLISH_PORT': 'none',
                'DOCKER_DATABASE': 'host',
            },
            warn=False,
        )
        self.assertNotIn('redis', published)

    def test_auth_compose_content_contains_requirepass(self) -> None:
        content = build_redis_auth_compose_content('s3cret!')
        self.assertIn('--requirepass', content)
        self.assertIn('s3cret!', content)
        self.assertIn('--no-auth-warning', content)

    def test_write_redis_auth_compose_removes_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'docker-compose.redis-auth.generated.yml'
            write_redis_auth_compose(path, 'pass')
            self.assertTrue(path.is_file())
            write_redis_auth_compose(path, '')
            self.assertFalse(path.is_file())


class InfraPublishBindTests(unittest.TestCase):
    def test_publish_compose_binds_loopback(self) -> None:
        content = build_publish_compose_content({'postgres': 5433, 'meilisearch': 8004})
        self.assertIn(f'{INFRA_PUBLISH_BIND}:5433:5432', content)
        self.assertIn(f'{INFRA_PUBLISH_BIND}:8004:7700', content)
        self.assertNotIn('- "5433:5432"', content)
        self.assertNotIn('- "8004:7700"', content)

    def test_empty_publish_has_no_wildcard_ports(self) -> None:
        content = build_publish_compose_content({})
        self.assertIn('services: {}', content)


class HostProfileComposeTests(unittest.TestCase):
    def test_full_includes_host_api_media_beat(self) -> None:
        profiles = compose_profiles({'HOST_PROFILE': 'full', 'ERGO_PROXY': 'none'})
        self.assertIn('host-api', profiles)
        self.assertIn('host-media', profiles)
        self.assertIn('host-beat', profiles)

    def test_modules_omits_host_api(self) -> None:
        profiles = compose_profiles({'HOST_PROFILE': 'modules', 'ERGO_PROXY': 'none'})
        self.assertNotIn('host-api', profiles)
        self.assertNotIn('host-beat', profiles)


if __name__ == '__main__':
    unittest.main()
