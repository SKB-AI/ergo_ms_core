"""Единый расчёт частоты загрузок (media + nginx)."""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from security.profile_defaults import merge_security_profile_defaults
from upload_rate import (
    build_rate_limit_conf,
    media_rate_to_nginx,
    resolve_upload_rates,
)


class UploadRateHelpersTests(unittest.TestCase):
    def test_media_rate_to_nginx(self) -> None:
        self.assertEqual(media_rate_to_nginx('60/minute'), '60r/m')
        self.assertEqual(media_rate_to_nginx('30/second'), '30r/s')

    def test_resolve_hardened_defaults(self) -> None:
        rates = resolve_upload_rates({'ERGO_SECURITY': 'hardened'})
        self.assertEqual(rates['user_rate'], '15/minute')
        self.assertEqual(rates['admin_rate'], '60/minute')
        self.assertEqual(rates['nginx_zone_rate'], '1000r/m')
        self.assertEqual(rates['burst'], 25)

    def test_resolve_standard_defaults(self) -> None:
        rates = resolve_upload_rates({})
        self.assertEqual(rates['user_rate'], '30/minute')
        self.assertEqual(rates['admin_rate'], '120/minute')
        self.assertEqual(rates['ceiling_rate'], '1000/minute')
        self.assertEqual(rates['nginx_zone_rate'], '1000r/m')

    def test_explicit_admin_rate(self) -> None:
        rates = resolve_upload_rates({
            'ERGO_SECURITY': 'hardened',
            'MEDIA_API_UPLOAD_RATE_ADMIN': '90/minute',
        })
        self.assertEqual(rates['admin_rate'], '90/minute')
        self.assertEqual(rates['nginx_zone_rate'], '1000r/m')

    def test_explicit_ceiling_below_admin(self) -> None:
        rates = resolve_upload_rates({
            'MEDIA_API_UPLOAD_RATE_ADMIN': '120/minute',
            'MEDIA_API_UPLOAD_RATE_CEILING': '60/minute',
        })
        self.assertEqual(rates['nginx_zone_rate'], '120r/m')

    def test_build_rate_limit_conf_no_hardcoded_5rm(self) -> None:
        text = build_rate_limit_conf({'ERGO_SECURITY': 'hardened'})
        self.assertNotIn('5r/m', text)
        self.assertIn('rate=1000r/m', text)
        self.assertIn('zone=ergo_upload', text)
        # Комментарий сниппета не должен содержать ${ERGO_RATE_LIMIT_CONF}:
        # иначе второй проход apply_template_replacements раздует блок.
        self.assertNotIn('${ERGO_RATE_LIMIT_CONF}', text)
        self.assertNotIn('${ERGO_UPLOAD_ZONE_RATE}', text)
        self.assertEqual(text.count('limit_req_zone $binary_remote_addr zone=ergo_upload'), 1)


class MergeAdminUploadRateTests(unittest.TestCase):
    def test_admin_rate_injected(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'hardened'})
        self.assertEqual(merged['MEDIA_API_UPLOAD_RATE_ADMIN'], '60/minute')
        merged_std = merge_security_profile_defaults({})
        self.assertEqual(merged_std['MEDIA_API_UPLOAD_RATE_ADMIN'], '120/minute')


if __name__ == '__main__':
    unittest.main()
