from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401


def _load_wait_module():
    path = Path(__file__).resolve().parents[1] / 'docker' / 'entrypoint' / 'wait_for_services.py'
    spec = importlib.util.spec_from_file_location('wait_for_services', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class WaitForServicesTests(unittest.TestCase):
    def test_skips_redis_when_broker_is_local(self) -> None:
        module = _load_wait_module()
        calls: list[str] = []

        def fake_wait(host, port, timeout, label):
            calls.append(label)
            return True

        env = {
            'DOCKER_ENABLED': 'true',
            'ERGO_DOCKER_SKIP_INFRA_WAIT': 'false',
            'REDIS_ENABLED': 'false',
            'ERGO_BROKER': 'local',
            'DOCKER_PROFILE_POSTGRES': 'false',
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(module, 'wait_tcp', fake_wait):
                self.assertEqual(module.main(), 0)
        self.assertNotIn('Redis', calls)

    def test_waits_redis_when_enabled(self) -> None:
        module = _load_wait_module()
        calls: list[str] = []

        def fake_wait(host, port, timeout, label):
            calls.append(label)
            return True

        env = {
            'DOCKER_ENABLED': 'true',
            'ERGO_DOCKER_SKIP_INFRA_WAIT': 'false',
            'REDIS_ENABLED': 'true',
            'ERGO_BROKER': 'redis',
            'DOCKER_PROFILE_POSTGRES': 'false',
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(module, 'wait_tcp', fake_wait):
                self.assertEqual(module.main(), 0)
        self.assertIn('Redis', calls)


if __name__ == '__main__':
    unittest.main()
