from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from docker_runtime import (  # noqa: E402
    build_compose_env_overrides,
    effective_db_host,
    effective_redis_compose_host,
    prepare_compose_artifacts,
)


class DockerRuntimeTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
