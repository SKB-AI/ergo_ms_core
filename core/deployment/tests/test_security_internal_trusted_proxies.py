"""Тесты контроля internal.trusted_proxies (security audit С3)."""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.checkers import _REGISTRY
from security.checkers.internal_trusted_proxies import run as trusted_proxies_run


class InternalTrustedProxiesCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('internal.trusted_proxies')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'internal_trusted_proxies')
        self.assertEqual(self.control.status, 'implemented')
        self.assertIn('internal_trusted_proxies', _REGISTRY)

    def _run(self, *, level: str, values: dict[str, str]):
        return trusted_proxies_run(
            self.control,
            self.catalog,
            {'values': values, 'level': level, 'root': '.'},
        )

    def test_open_ok(self) -> None:
        finding = self._run(level='open', values={})
        self.assertEqual(finding.severity, 'ok')

    def test_standard_ok(self) -> None:
        finding = self._run(level='standard', values={})
        self.assertEqual(finding.severity, 'ok')

    def test_hardened_ok_empty_list(self) -> None:
        finding = self._run(level='hardened', values={})
        self.assertEqual(finding.severity, 'ok')
        self.assertIn('пуст', finding.message)
        self.assertNotEqual(finding.severity, 'skip')

    def test_hardened_ok_with_list(self) -> None:
        finding = self._run(
            level='hardened',
            values={'MEDIA_API_TRUSTED_PROXIES': '10.0.0.1,10.0.0.0/8'},
        )
        self.assertEqual(finding.severity, 'ok')
        self.assertIn('задан', finding.message)

    def test_maximum_error_without_internal_key(self) -> None:
        finding = self._run(
            level='maximum',
            values={'API_SECRET_KEY': 'api-secret-value-here'},
        )
        self.assertEqual(finding.severity, 'error')
        self.assertIn('MEDIA_API_INTERNAL_KEY', finding.message)

    def test_maximum_error_when_key_equals_api_secret(self) -> None:
        finding = self._run(
            level='maximum',
            values={
                'API_SECRET_KEY': 'same-secret-value',
                'MEDIA_API_INTERNAL_KEY': 'same-secret-value',
            },
        )
        self.assertEqual(finding.severity, 'error')
        self.assertIn('совпадает', finding.message)

    def test_maximum_ok_separate_key(self) -> None:
        finding = self._run(
            level='maximum',
            values={
                'API_SECRET_KEY': 'api-secret-value-here',
                'MEDIA_API_INTERNAL_KEY': 'media-internal-separate',
            },
        )
        self.assertEqual(finding.severity, 'ok')
        self.assertIn('отличается', finding.message)


if __name__ == '__main__':
    unittest.main()
