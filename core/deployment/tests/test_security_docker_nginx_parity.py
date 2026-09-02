"""Тесты checker deploy.docker_nginx_parity (С6)."""

from __future__ import annotations

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.checkers import _REGISTRY, run_control_check


class DockerNginxParityCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[3]
        cls.catalog = load_security_catalog()
        cls.control = cls.catalog.control_by_id('deploy.docker_nginx_parity')

    def test_registry_and_catalog(self) -> None:
        self.assertIn('docker_nginx_parity', _REGISTRY)
        self.assertEqual(self.control.check, 'docker_nginx_parity')
        self.assertEqual(self.control.status, 'implemented')

    def test_optional_on_standard(self) -> None:
        finding = run_control_check(
            self.control,
            self.catalog,
            {'values': {}, 'level': 'standard', 'root': self.root},
        )
        self.assertEqual(finding.severity, 'ok')

    def test_required_hardened_ok(self) -> None:
        finding = run_control_check(
            self.control,
            self.catalog,
            {'values': {}, 'level': 'hardened', 'root': self.root},
        )
        self.assertEqual(finding.severity, 'ok', msg=finding.message)


if __name__ == '__main__':
    unittest.main()
