from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from ergo_modes import ergo_security, ergo_security_enforce, security_level_rank
from security.catalog import load_security_catalog
from security.cli_check import build_security_report, run_security_check
from security.levels import LEVEL_ORDER, normalize_security_level


class ErgoSecurityModesTests(unittest.TestCase):
    def test_default_level_standard(self) -> None:
        self.assertEqual(ergo_security({}), 'standard')
        self.assertEqual(ergo_security({'ERGO_SECURITY': 'nope'}), 'standard')

    def test_normalize_unknown(self) -> None:
        self.assertEqual(normalize_security_level('weird'), 'standard')
        self.assertEqual(security_level_rank('hardened'), 2)

    def test_enforce_default_warn(self) -> None:
        self.assertEqual(ergo_security_enforce({}), 'warn')
        self.assertEqual(ergo_security_enforce({'ERGO_SECURITY_ENFORCE': 'raise'}), 'raise')


class CatalogIntegrityTests(unittest.TestCase):
    def test_catalog_loads_unique_ids_four_profiles(self) -> None:
        catalog = load_security_catalog()
        ids = [c.id for c in catalog.controls]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 40)
        for control in catalog.controls:
            for level in LEVEL_ORDER:
                self.assertIn(level, control.profiles)


class SecurityCheckTests(unittest.TestCase):
    def test_open_in_production_error(self) -> None:
        values = {
            'ERGO_ENV': 'production',
            'ERGO_SECURITY': 'open',
            'API_SECRET_KEY': 'unique-long-secret-key-value-32chars',
            'CORS_ALLOWED_ORIGINS': 'https://app.example.com',
            'CSRF_TRUSTED_ORIGINS': 'https://app.example.com',
        }
        report = build_security_report(Path('.'), values=values)
        meta = [f for f in report.findings if f.control_id == 'meta.open_in_production']
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0].severity, 'error')

    def test_secrets_template_error_without_leaking_value(self) -> None:
        secret = 'secret_key'
        values = {
            'ERGO_ENV': 'development',
            'ERGO_SECURITY': 'standard',
            'API_SECRET_KEY': secret,
        }
        report = build_security_report(Path('.'), values=values)
        finding = next(f for f in report.findings if f.control_id == 'secrets.no_defaults')
        self.assertEqual(finding.severity, 'error')
        self.assertNotIn(secret, finding.message)
        dumped = json.dumps(report.to_dict(), ensure_ascii=False)
        self.assertNotIn(f'API_SECRET_KEY={secret}', dumped)

    def test_cors_empty_production_error(self) -> None:
        values = {
            'ERGO_ENV': 'production',
            'ERGO_SECURITY': 'standard',
            'API_SECRET_KEY': 'unique-long-secret-key-value-32chars',
            'CORS_ALLOWED_ORIGINS': '',
            'CSRF_TRUSTED_ORIGINS': 'https://app.example.com',
        }
        report = build_security_report(Path('.'), values=values)
        cors = next(f for f in report.findings if f.control_id == 'cors.explicit_origins')
        self.assertEqual(cors.severity, 'error')

    def test_cors_empty_development_ok(self) -> None:
        values = {
            'ERGO_ENV': 'development',
            'ERGO_SECURITY': 'standard',
            'API_SECRET_KEY': 'unique-long-secret-key-value-32chars',
        }
        report = build_security_report(Path('.'), values=values)
        cors = next(f for f in report.findings if f.control_id == 'cors.explicit_origins')
        self.assertEqual(cors.severity, 'ok')

    def test_unknown_check_skip_not_fatal_catalog(self) -> None:
        catalog = load_security_catalog()
        control = catalog.controls[0]
        from security.checkers import run_control_check

        broken = type(control)(
            **{
                **control.__dict__,
                'check': 'does_not_exist_runner',
            }
        )
        finding = run_control_check(broken, catalog, {'values': {}, 'level': 'standard'})
        self.assertEqual(finding.severity, 'skip')

    def test_profile_changes_target(self) -> None:
        values = {
            'ERGO_ENV': 'development',
            'ERGO_SECURITY': 'standard',
            'API_SECRET_KEY': 'unique-long-secret-key-value-32chars',
            'API_ACCESS_TOKEN_LIFETIME': '45',
        }
        std = build_security_report(Path('.'), values=values, profile='standard')
        hard = build_security_report(Path('.'), values=values, profile='hardened')
        self.assertEqual(std.level, 'standard')
        self.assertEqual(hard.level, 'hardened')
        hard_ttl = next(f for f in hard.findings if f.control_id == 'token.access_ttl_max')
        self.assertEqual(hard_ttl.severity, 'warning')

    def test_enforce_off_exit_zero_with_errors(self) -> None:
        values = {
            'ERGO_ENV': 'production',
            'ERGO_SECURITY': 'standard',
            'API_SECRET_KEY': 'secret_key',
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('ERGO_ENV=production\n', encoding='utf-8')
            with patch('security.cli_check.load_merged_env', return_value=values):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_security_check(root, enforce='off', as_json=True)
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
