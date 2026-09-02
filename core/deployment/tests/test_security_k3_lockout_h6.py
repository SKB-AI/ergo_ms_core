from __future__ import annotations

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.cli_check import build_security_report
from security.profile_defaults import merge_security_profile_defaults
from security.secret_validation import (
    MIN_PRODUCTION_SECRET_LENGTH,
    is_insecure_secret,
    validate_production_secret_key,
)


class SecretValidationTests(unittest.TestCase):
    def test_template_values_insecure(self) -> None:
        self.assertTrue(is_insecure_secret('secret_key'))
        self.assertTrue(is_insecure_secret(''))
        self.assertTrue(is_insecure_secret('secret-key'))
        self.assertFalse(is_insecure_secret('a' * MIN_PRODUCTION_SECRET_LENGTH))

    def test_production_raise_without_leaking(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_production_secret_key('secret_key')
        msg = str(ctx.exception)
        self.assertNotIn('API_SECRET_KEY=secret_key', msg)
        self.assertIn('ERGO_ENV=production', msg)
        self.assertIn('generate-secret', msg)

    def test_production_short_key(self) -> None:
        with self.assertRaises(ValueError):
            validate_production_secret_key('short-but-unique-key')

    def test_production_ok(self) -> None:
        validate_production_secret_key('unique-long-secret-key-value-32chars!!')


class SecretsCatalogTests(unittest.TestCase):
    def test_control_implemented(self) -> None:
        control = load_security_catalog().control_by_id('secrets.no_defaults')
        self.assertIsNotNone(control)
        self.assertEqual(control.status, 'implemented')

    def test_empty_secret_standard_error(self) -> None:
        values = {
            'ERGO_ENV': 'development',
            'ERGO_SECURITY': 'standard',
            'API_SECRET_KEY': '',
        }
        report = build_security_report(Path('.'), values=values)
        finding = next(f for f in report.findings if f.control_id == 'secrets.no_defaults')
        self.assertEqual(finding.severity, 'error')


class LockoutRetentionInjectTests(unittest.TestCase):
    def test_standard_injects_lockout(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'standard'})
        self.assertEqual(merged['API_AUTH_LOCKOUT_MAX_ATTEMPTS'], '10')
        self.assertEqual(merged['API_SESSION_DEVICE_RETENTION_DAYS'], '0')

    def test_hardened_injects(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'hardened'})
        self.assertEqual(merged['API_AUTH_LOCKOUT_MAX_ATTEMPTS'], '10')
        self.assertEqual(merged['API_SESSION_DEVICE_RETENTION_DAYS'], '90')

    def test_maximum_injects(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'maximum'})
        self.assertEqual(merged['API_AUTH_LOCKOUT_MAX_ATTEMPTS'], '5')
        self.assertEqual(merged['API_SESSION_DEVICE_RETENTION_DAYS'], '30')

    def test_explicit_kept(self) -> None:
        merged = merge_security_profile_defaults({
            'ERGO_SECURITY': 'hardened',
            'API_AUTH_LOCKOUT_MAX_ATTEMPTS': '3',
            'API_SESSION_DEVICE_RETENTION_DAYS': '14',
        })
        self.assertEqual(merged['API_AUTH_LOCKOUT_MAX_ATTEMPTS'], '3')
        self.assertEqual(merged['API_SESSION_DEVICE_RETENTION_DAYS'], '14')

    def test_lockout_zero_on_standard_errors(self) -> None:
        values = {
            'ERGO_ENV': 'development',
            'ERGO_SECURITY': 'standard',
            'API_SECRET_KEY': 'unique-long-secret-key-value-32chars',
            'API_AUTH_LOCKOUT_MAX_ATTEMPTS': '0',
        }
        report = build_security_report(Path('.'), values=values)
        finding = next(f for f in report.findings if f.control_id == 'auth.lockout')
        self.assertEqual(finding.severity, 'error')

    def test_lockout_zero_on_hardened_errors(self) -> None:
        values = {
            'ERGO_ENV': 'development',
            'ERGO_SECURITY': 'hardened',
            'API_SECRET_KEY': 'unique-long-secret-key-value-32chars',
            'API_AUTH_LOCKOUT_MAX_ATTEMPTS': '0',
        }
        report = build_security_report(Path('.'), values=values)
        finding = next(f for f in report.findings if f.control_id == 'auth.lockout')
        self.assertEqual(finding.severity, 'error')

    def test_controls_implemented(self) -> None:
        catalog = load_security_catalog()
        self.assertEqual(catalog.control_by_id('auth.lockout').status, 'implemented')
        self.assertEqual(
            catalog.control_by_id('session.device_retention').status,
            'implemented',
        )


if __name__ == '__main__':
    unittest.main()
