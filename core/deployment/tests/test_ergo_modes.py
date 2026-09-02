from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from ergo_modes import (  # noqa: E402
    effective_docker_enabled,
    effective_docker_profile_jupyter,
    effective_docker_profile_postgres,
    effective_jupyter_behind_nginx,
    effective_nginx_enabled,
    effective_redis_enabled,
    env_bool,
    ergo_broker,
    ergo_proxy,
    ergo_runtime,
)


class ErgoModesTests(unittest.TestCase):
    def test_env_bool(self) -> None:
        self.assertTrue(env_bool('true'))
        self.assertTrue(env_bool('ON'))
        self.assertFalse(env_bool(''))
        self.assertFalse(env_bool(None))
        self.assertTrue(env_bool('', default=True))

    def test_ergo_proxy_and_nginx_effective(self) -> None:
        self.assertEqual(ergo_proxy({'ERGO_PROXY': 'nginx'}), 'nginx')
        self.assertTrue(effective_nginx_enabled({'ERGO_PROXY': 'nginx'}))
        self.assertFalse(effective_nginx_enabled({'ERGO_PROXY': 'none'}))
        self.assertTrue(effective_nginx_enabled({'NGINX_ENABLED': 'true', 'ERGO_PROXY': 'none'}))

    def test_ergo_broker_and_redis_effective(self) -> None:
        self.assertEqual(ergo_broker({'ERGO_BROKER': 'redis'}), 'redis')
        self.assertTrue(effective_redis_enabled({'ERGO_BROKER': 'redis'}))
        self.assertFalse(effective_redis_enabled({'REDIS_ENABLED': 'false', 'ERGO_BROKER': 'redis'}))

    def test_docker_runtime_and_profiles(self) -> None:
        self.assertEqual(ergo_runtime({'ERGO_RUNTIME': 'docker'}), 'docker')
        self.assertTrue(effective_docker_enabled({'ERGO_RUNTIME': 'docker'}))
        self.assertFalse(effective_docker_enabled({'DOCKER_ENABLED': 'false', 'ERGO_RUNTIME': 'docker'}))
        self.assertTrue(effective_docker_profile_postgres({'ERGO_DB': 'portable_postgres'}))
        self.assertTrue(effective_docker_profile_jupyter({'ERGO_JUPYTER': 'local'}))
        self.assertFalse(effective_docker_profile_jupyter({'ERGO_JUPYTER': 'none'}))

    def test_jupyter_behind_nginx_follows_ergo_modes(self) -> None:
        self.assertTrue(effective_jupyter_behind_nginx({
            'ERGO_PROXY': 'nginx',
            'ERGO_JUPYTER': 'nginx',
            'API_JUPYTER_BEHIND_NGINX': 'false',
        }))
        self.assertFalse(effective_jupyter_behind_nginx({
            'ERGO_PROXY': 'none',
            'ERGO_JUPYTER': 'nginx',
        }))
        self.assertTrue(effective_jupyter_behind_nginx({
            'ERGO_PROXY': 'nginx',
            'ERGO_JUPYTER': 'auto',
            'API_JUPYTER_BEHIND_NGINX': 'true',
        }))
        self.assertFalse(effective_jupyter_behind_nginx({
            'ERGO_PROXY': 'nginx',
            'ERGO_JUPYTER': 'auto',
            'API_JUPYTER_BEHIND_NGINX': 'false',
        }))


if __name__ == '__main__':
    unittest.main()
