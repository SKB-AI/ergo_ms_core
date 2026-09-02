from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from security.profile_defaults import merge_security_profile_defaults


class MergeSecurityProfileDefaultsTests(unittest.TestCase):
    def test_unset_standard_gets_profile_values(self) -> None:
        merged = merge_security_profile_defaults({})
        self.assertEqual(merged['API_THROTTLE_RATES_LOGIN'], '5/minute')
        self.assertEqual(merged['API_PASSWORD_MIN_LENGTH'], '8')
        self.assertEqual(merged['API_JWT_LIFETIME_ENABLED'], 'true')
        self.assertEqual(merged['API_REMEMBER_ME_REFRESH_TOKEN_LIFETIME'], '10080')
        self.assertEqual(merged['MEDIA_URL_EXPIRATION'], '3600')
        self.assertEqual(merged['MEDIA_API_UPLOAD_RATE'], '30/minute')
        self.assertEqual(merged['MEDIA_API_UPLOAD_RATE_ADMIN'], '120/minute')
        self.assertEqual(merged['MEDIA_API_CONTENT_VALIDATION'], 'extension_and_magic')
        self.assertEqual(merged['CLIENT_BROWSER_LOG_ENABLED'], 'true')
        self.assertEqual(merged['API_ADP_DEFAULT_VIEW_GRANTS'], 'granted')
        self.assertEqual(merged['API_CSP_MODE'], 'as_is')
        self.assertEqual(merged['API_AUTH_LOCKOUT_MAX_ATTEMPTS'], '10')
        self.assertEqual(merged['API_SESSION_DEVICE_RETENTION_DAYS'], '0')

    def test_explicit_values_kept(self) -> None:
        values = {
            'ERGO_SECURITY': 'standard',
            'API_THROTTLE_RATES_LOGIN': '99/minute',
            'API_PASSWORD_MIN_LENGTH': '10',
            'MEDIA_API_UPLOAD_RATE': '1/minute',
        }
        merged = merge_security_profile_defaults(values)
        self.assertEqual(merged['API_THROTTLE_RATES_LOGIN'], '99/minute')
        self.assertEqual(merged['API_PASSWORD_MIN_LENGTH'], '10')
        self.assertEqual(merged['MEDIA_API_UPLOAD_RATE'], '1/minute')
        self.assertEqual(merged['API_JWT_LIFETIME_ENABLED'], 'true')

    def test_open_vs_standard_differences(self) -> None:
        open_merged = merge_security_profile_defaults({'ERGO_SECURITY': 'open'})
        std_merged = merge_security_profile_defaults({'ERGO_SECURITY': 'standard'})
        self.assertEqual(open_merged['API_THROTTLE_RATES_LOGIN'], '100/minute')
        self.assertEqual(std_merged['API_THROTTLE_RATES_LOGIN'], '5/minute')
        self.assertEqual(open_merged['API_PASSWORD_MIN_LENGTH'], '6')
        self.assertEqual(std_merged['API_PASSWORD_MIN_LENGTH'], '8')
        self.assertEqual(open_merged['MEDIA_API_UPLOAD_RATE'], '100/minute')
        self.assertEqual(std_merged['MEDIA_API_UPLOAD_RATE'], '30/minute')
        self.assertEqual(open_merged['MEDIA_API_UPLOAD_RATE_ADMIN'], '300/minute')
        self.assertEqual(std_merged['MEDIA_API_UPLOAD_RATE_ADMIN'], '120/minute')

    def test_whitespace_treated_as_unset(self) -> None:
        merged = merge_security_profile_defaults({
            'ERGO_SECURITY': 'standard',
            'API_THROTTLE_RATES_LOGIN': '  ',
            'API_PASSWORD_MIN_LENGTH': '',
        })
        self.assertEqual(merged['API_THROTTLE_RATES_LOGIN'], '5/minute')
        self.assertEqual(merged['API_PASSWORD_MIN_LENGTH'], '8')

    def test_merge_does_not_write_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / '.env'
            env_path.write_text('ERGO_ENV=development\n', encoding='utf-8')
            before = env_path.read_text(encoding='utf-8')
            with patch('pathlib.Path.write_text') as write_mock:
                merge_security_profile_defaults({'ERGO_SECURITY': 'standard'})
                write_mock.assert_not_called()
            self.assertEqual(env_path.read_text(encoding='utf-8'), before)

    def test_semantic_keys_not_invented(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'standard'})
        self.assertNotIn('CORS_ALLOWED_ORIGINS', merged)
        self.assertNotIn('CSRF_TRUSTED_ORIGINS', merged)
        self.assertNotIn('API_REGISTRATION_MODE', merged)
        self.assertNotIn('API_SECRET_KEY', merged)
        self.assertNotIn('API_ACCESS_TOKEN_LIFETIME', merged)
        self.assertNotIn('REDIS_PASSWORD', merged)

    def test_jwt_lifetime_not_forced_on_open(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'open'})
        self.assertNotIn('API_JWT_LIFETIME_ENABLED', merged)

    def test_hardened_follows_catalog_where_different(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'hardened'})
        self.assertEqual(merged['API_PASSWORD_MIN_LENGTH'], '12')
        self.assertEqual(merged['API_REMEMBER_ME_REFRESH_TOKEN_LIFETIME'], '1440')
        self.assertEqual(merged['MEDIA_URL_EXPIRATION'], '900')
        self.assertEqual(merged['MEDIA_API_UPLOAD_RATE'], '15/minute')
        self.assertEqual(merged['MEDIA_API_UPLOAD_RATE_ADMIN'], '60/minute')
        self.assertEqual(merged['MEDIA_API_CONTENT_VALIDATION'], 'extension_and_magic')
        self.assertEqual(merged['API_JWT_LIFETIME_ENABLED'], 'true')
        self.assertEqual(merged['API_ADP_DEFAULT_VIEW_GRANTS'], 'denied')
        self.assertEqual(merged['API_CSP_MODE'], 'no_unsafe')
        self.assertEqual(merged['API_AUTH_LOCKOUT_MAX_ATTEMPTS'], '10')
        self.assertEqual(merged['API_SESSION_DEVICE_RETENTION_DAYS'], '90')

    def test_input_not_mutated(self) -> None:
        values = {'ERGO_SECURITY': 'open'}
        merged = merge_security_profile_defaults(values)
        self.assertNotIn('API_THROTTLE_RATES_LOGIN', values)
        self.assertEqual(merged['API_THROTTLE_RATES_LOGIN'], '100/minute')


if __name__ == '__main__':
    unittest.main()
