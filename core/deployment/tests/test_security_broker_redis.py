"""Тесты контроля broker.redis_password (security audit V5)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.checkers import _REGISTRY
from security.checkers.broker_redis import run as broker_redis_run


class BrokerRedisPasswordCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('broker.redis_password')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'broker_redis_password')
        self.assertEqual(self.control.status, 'implemented')
        self.assertIn('broker_redis_password', _REGISTRY)

    def _run(
        self,
        *,
        level: str,
        values: dict[str, str],
        yaml_password: str | None = None,
        has_redis_section: bool = True,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if has_redis_section:
                password_line = '' if yaml_password is None else f'    password: "{yaml_password}"\n'
                (root / 'databases.yaml').write_text(
                    'databases:\n'
                    '  redis:\n'
                    '    host: 127.0.0.1\n'
                    '    port: 6379\n'
                    f'{password_line}',
                    encoding='utf-8',
                )
            return broker_redis_run(
                self.control,
                self.catalog,
                {'values': values, 'level': level, 'root': root},
            )

    def test_skip_when_redis_not_used(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_BROKER': 'local'},
            yaml_password='',
        )
        self.assertEqual(finding.severity, 'ok')
        self.assertIn('не используется', finding.message)

    def test_open_ok_without_password(self) -> None:
        finding = self._run(
            level='open',
            values={'ERGO_BROKER': 'redis'},
            yaml_password='',
        )
        self.assertEqual(finding.severity, 'ok')

    def test_standard_warning_without_password(self) -> None:
        finding = self._run(
            level='standard',
            values={'ERGO_BROKER': 'redis'},
            yaml_password='',
        )
        self.assertEqual(finding.severity, 'warning')

    def test_hardened_error_without_password(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_BROKER': 'redis'},
            yaml_password='',
        )
        self.assertEqual(finding.severity, 'error')

    def test_maximum_error_without_password(self) -> None:
        finding = self._run(
            level='maximum',
            values={'ERGO_BROKER': 'redis'},
            yaml_password='',
        )
        self.assertEqual(finding.severity, 'error')

    def test_maximum_ok_with_password_no_publish(self) -> None:
        finding = self._run(
            level='maximum',
            values={'ERGO_BROKER': 'redis', 'DOCKER_REDIS_PUBLISH_PORT': ''},
            yaml_password='secret',
        )
        self.assertEqual(finding.severity, 'ok')

    def test_maximum_error_when_port_published(self) -> None:
        finding = self._run(
            level='maximum',
            values={'ERGO_BROKER': 'redis', 'DOCKER_REDIS_PUBLISH_PORT': '6379'},
            yaml_password='secret',
        )
        self.assertEqual(finding.severity, 'error')
        self.assertIn('DOCKER_REDIS_PUBLISH_PORT', finding.message)

    def test_hardened_ok_with_password_even_if_published(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_BROKER': 'redis', 'DOCKER_REDIS_PUBLISH_PORT': '6379'},
            yaml_password='secret',
        )
        self.assertEqual(finding.severity, 'ok')

    def test_legacy_redis_enabled(self) -> None:
        finding = self._run(
            level='standard',
            values={'REDIS_ENABLED': 'true'},
            yaml_password='',
        )
        self.assertEqual(finding.severity, 'warning')


if __name__ == '__main__':
    unittest.main()
