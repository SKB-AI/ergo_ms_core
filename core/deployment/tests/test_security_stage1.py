from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.checkers import _REGISTRY, run_control_check
from security.checkers.anonymous_endpoints import (
    load_anonymous_allowlist,
    run as anonymous_endpoints_run,
    scan_core_anonymous_views,
)
from security.checkers.jupyter_exposure import run as jupyter_exposure_run
from security.checkers.password_policy import run as password_policy_run
from security.cli_check import build_security_report


class RegistryStage1Tests(unittest.TestCase):
    def test_new_check_ids_registered(self) -> None:
        for check_id in (
            'password_policy',
            'jupyter_exposure',
            'anonymous_endpoints',
            'client_browser_log',
        ):
            self.assertIn(check_id, _REGISTRY)

    def test_unknown_check_still_skip(self) -> None:
        catalog = load_security_catalog()
        control = catalog.controls[0]
        broken = type(control)(**{**control.__dict__, 'check': 'no_such_runner_stage1'})
        finding = run_control_check(broken, catalog, {'values': {}, 'level': 'standard'})
        self.assertEqual(finding.severity, 'skip')


class PasswordPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('password.policy')
        self.catalog = catalog
        self.assertIsNotNone(self.control)

    def test_default_ok_on_standard(self) -> None:
        finding = password_policy_run(
            self.control,
            self.catalog,
            {'values': {}, 'level': 'standard'},
        )
        self.assertEqual(finding.severity, 'ok')

    def test_short_min_length_error(self) -> None:
        finding = password_policy_run(
            self.control,
            self.catalog,
            {'values': {'API_PASSWORD_MIN_LENGTH': '4'}, 'level': 'standard'},
        )
        self.assertEqual(finding.severity, 'error')
        self.assertIn('min_length', finding.message)

    def test_digit_false_error_on_standard(self) -> None:
        finding = password_policy_run(
            self.control,
            self.catalog,
            {
                'values': {
                    'API_PASSWORD_MIN_LENGTH': '8',
                    'API_PASSWORD_REQUIRE_DIGIT': 'false',
                },
                'level': 'standard',
            },
        )
        self.assertEqual(finding.severity, 'error')
        self.assertIn('DIGIT', finding.message)


class JupyterExposureTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('jupyter.exposure')
        self.catalog = catalog
        self.assertIsNotNone(self.control)

    def test_disabled_ok(self) -> None:
        finding = jupyter_exposure_run(
            self.control,
            self.catalog,
            {'values': {'ERGO_JUPYTER': 'none'}, 'level': 'standard'},
        )
        self.assertEqual(finding.severity, 'ok')

    def test_lan_without_token_warning(self) -> None:
        finding = jupyter_exposure_run(
            self.control,
            self.catalog,
            {
                'values': {
                    'ERGO_JUPYTER': 'lan',
                    'API_JUPYTER_TOKEN': '',
                },
                'level': 'standard',
            },
        )
        self.assertEqual(finding.severity, 'warning')
        self.assertNotIn('token=', finding.message.lower())

    def test_lan_with_token_ok(self) -> None:
        secret = 'jupyter-test-token-not-for-production'
        finding = jupyter_exposure_run(
            self.control,
            self.catalog,
            {
                'values': {
                    'ERGO_JUPYTER': 'lan',
                    'API_JUPYTER_TOKEN': secret,
                },
                'level': 'standard',
            },
        )
        self.assertEqual(finding.severity, 'ok')
        self.assertNotIn(secret, finding.message)


class AnonymousAllowlistTests(unittest.TestCase):
    def test_allowlist_parses(self) -> None:
        allowlist = load_anonymous_allowlist()
        self.assertIn('UserAuthorizationView', allowlist.names())
        self.assertIn('ReadyView', allowlist.names())
        self.assertIn('ThemeViewSet', allowlist.names())
        self.assertIn('JupyterAccessView', allowlist.names())
        self.assertIn('DeviceBoundTokenRefreshView', allowlist.names())

    def test_scan_temp_tree_base_api_view_not_anonymous(self) -> None:
        catalog = load_security_catalog()
        control = catalog.control_by_id('api.anonymous_endpoints')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = root / 'pkg'
            core.mkdir()
            (core / 'views.py').write_text(
                'from src.core.utils.base.base_views import BaseAPIView\n'
                'class AuthenticatedByDefaultView(BaseAPIView):\n'
                '    pass\n',
                encoding='utf-8',
            )
            finding = anonymous_endpoints_run(
                control,
                catalog,
                {
                    'values': {},
                    'level': 'standard',
                    'root': root,
                    'core_api_root': core,
                },
            )
            self.assertEqual(finding.severity, 'ok', msg=finding.message)

    def test_scan_temp_tree_public_mixin_extra(self) -> None:
        catalog = load_security_catalog()
        control = catalog.control_by_id('api.anonymous_endpoints')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = root / 'pkg'
            core.mkdir()
            (core / 'views.py').write_text(
                'from src.core.utils.base.base_views import BaseAPIViewPublicMixin\n'
                'class LeakyPublicView(BaseAPIViewPublicMixin):\n'
                '    pass\n',
                encoding='utf-8',
            )
            finding = anonymous_endpoints_run(
                control,
                catalog,
                {
                    'values': {},
                    'level': 'standard',
                    'root': root,
                    'core_api_root': core,
                },
            )
            self.assertEqual(finding.severity, 'error')
            self.assertIn('LeakyPublicView', finding.message)

    def test_real_core_allowlist_ok(self) -> None:
        catalog = load_security_catalog()
        control = catalog.control_by_id('api.anonymous_endpoints')
        project_root = Path(__file__).resolve().parents[3]
        finding = anonymous_endpoints_run(
            control,
            catalog,
            {
                'values': {},
                'level': 'standard',
                'root': project_root,
            },
        )
        self.assertEqual(
            finding.severity,
            'ok',
            msg=finding.message,
        )
        found = scan_core_anonymous_views(project_root / 'core' / 'api' / 'src' / 'core')
        names = {f.name for f in found}
        self.assertIn('UserAuthorizationView', names)
        self.assertIn('ThemeViewSet', names)
        self.assertIn('ReadyView', names)


class LoginThrottleDefaultTests(unittest.TestCase):
    def test_auth_py_default_five_per_minute(self) -> None:
        auth_path = (
            Path(__file__).resolve().parents[3]
            / 'core'
            / 'api'
            / 'src'
            / 'config'
            / 'settings'
            / 'auth.py'
        )
        source = auth_path.read_text(encoding='utf-8')
        self.assertIn('login_throttle_rate()', source)
        self.assertIn('security_profile_runtime', source)
        self.assertIn(
            "env.str(\n    'API_THROTTLE_RATES_PASSWORD_RESET',\n    default='5/minute',\n)",
            source,
        )
        # Синтаксис файла валиден
        ast.parse(source)

    def test_catalog_standard_login_throttle(self) -> None:
        catalog = load_security_catalog()
        control = catalog.control_by_id('auth.login_throttle')
        self.assertEqual(control.requirement('standard'), '5/minute')
        self.assertEqual(control.status, 'implemented')


class CatalogTruthUpTests(unittest.TestCase):
    def test_stage1_controls_not_deferred(self) -> None:
        catalog = load_security_catalog()
        expectations = {
            'api.default_permission': 'code_fixed',
            'headers.baseline': 'code_fixed',
            'auth.reset_code_policy': 'reset_code_policy',
            'media.upload_rate': 'env_rate_max',
            'media.upload_rate_admin': 'env_rate_max',
            'media.signed_urls_ttl': 'env_int_max',
            'logging.client_browser': 'client_browser_log',
            'password.policy': 'password_policy',
            'jupyter.exposure': 'jupyter_exposure',
            'api.anonymous_endpoints': 'anonymous_endpoints',
        }
        for control_id, check in expectations.items():
            control = catalog.control_by_id(control_id)
            self.assertIsNotNone(control, control_id)
            self.assertEqual(control.check, check, control_id)
            self.assertNotEqual(control.status, 'planned', control_id)

    def test_media_env_keys(self) -> None:
        catalog = load_security_catalog()
        signed = catalog.control_by_id('media.signed_urls_ttl')
        upload = catalog.control_by_id('media.upload_rate')
        upload_admin = catalog.control_by_id('media.upload_rate_admin')
        content = catalog.control_by_id('media.content_validation')
        self.assertEqual(signed.env_key, 'MEDIA_URL_EXPIRATION')
        self.assertEqual(upload.env_key, 'MEDIA_API_UPLOAD_RATE')
        self.assertEqual(upload_admin.env_key, 'MEDIA_API_UPLOAD_RATE_ADMIN')
        self.assertEqual(upload_admin.check, 'env_rate_max')
        self.assertEqual(content.env_key, 'MEDIA_API_CONTENT_VALIDATION')
        self.assertEqual(content.check, 'media_content_validation')
        self.assertEqual(content.status, 'partial')


class SecurityCheckSmokeTests(unittest.TestCase):
    def test_standard_fewer_skips_for_closed_controls(self) -> None:
        values = {
            'ERGO_ENV': 'development',
            'ERGO_SECURITY': 'standard',
            'API_SECRET_KEY': 'unique-long-secret-key-value-32chars',
            'ERGO_JUPYTER': 'none',
        }
        report = build_security_report(Path('.'), values=values, profile='standard')
        by_id = {f.control_id: f for f in report.findings}
        for control_id in (
            'password.policy',
            'jupyter.exposure',
            'api.anonymous_endpoints',
            'logging.client_browser',
            'api.default_permission',
            'headers.baseline',
            'auth.reset_code_policy',
            'auth.login_throttle',
        ):
            finding = by_id[control_id]
            self.assertNotEqual(
                finding.severity,
                'skip',
                msg=f'{control_id}: {finding.message}',
            )


if __name__ == '__main__':
    unittest.main()
