from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from upload_limits import (  # noqa: E402
    DEFAULT_HARD_MAX_BYTES,
    DEFAULT_MEDIA_UPLOAD_BYTES,
    compute_client_max_body_bytes,
    format_nginx_body_size,
    parse_direct_upload_bytes,
    parse_hard_max_bytes,
    parse_media_upload_bytes,
    parse_module_ceiling_bytes,
    parse_modules_max_bytes,
)


class UploadLimitsTests(unittest.TestCase):
    def test_parse_media_default(self) -> None:
        self.assertEqual(parse_media_upload_bytes({}), DEFAULT_MEDIA_UPLOAD_BYTES)

    def test_parse_media_custom(self) -> None:
        self.assertEqual(
            parse_media_upload_bytes({'MEDIA_UPLOAD_MAX_SIZE': '104857600'}),
            104857600,
        )

    def test_parse_hard_default(self) -> None:
        self.assertEqual(parse_hard_max_bytes({}), DEFAULT_HARD_MAX_BYTES)

    def test_parse_hard_not_below_media(self) -> None:
        env = {
            'MEDIA_UPLOAD_MAX_SIZE': str(800 * 1024 * 1024),
            'MEDIA_UPLOAD_HARD_MAX_SIZE': str(100 * 1024 * 1024),
        }
        self.assertEqual(parse_hard_max_bytes(env), 800 * 1024 * 1024)

    def test_parse_direct_empty_env(self) -> None:
        self.assertEqual(parse_direct_upload_bytes({}), 0)

    def test_parse_direct_discovers_attachment_key(self) -> None:
        self.assertEqual(
            parse_direct_upload_bytes({'SAMPLE_MOD_MAX_ATTACHMENT_SIZE_MB': '100'}),
            100 * 1024 * 1024,
        )

    def test_video_zero_means_hard(self) -> None:
        env = {
            'MEDIA_UPLOAD_HARD_MAX_SIZE': str(10 * 1024 * 1024 * 1024),
            'CLIENT_VIDEO_UPLOAD_MAX_SIZE_MB': '0',
        }
        self.assertEqual(
            parse_module_ceiling_bytes(
                env, 'CLIENT_VIDEO_UPLOAD_MAX_SIZE_MB', 0, zero_means_hard=True,
            ),
            10 * 1024 * 1024 * 1024,
        )

    def test_module_can_exceed_default_under_hard(self) -> None:
        env = {
            'MEDIA_UPLOAD_MAX_SIZE': str(500 * 1024 * 1024),
            'MEDIA_UPLOAD_HARD_MAX_SIZE': str(20 * 1024 * 1024 * 1024),
            'CLIENT_BI_UPLOAD_MAX_SIZE_MB': '800',
        }
        self.assertEqual(
            parse_module_ceiling_bytes(
                env, 'CLIENT_BI_UPLOAD_MAX_SIZE_MB', 200, zero_means_hard=False,
            ),
            800 * 1024 * 1024,
        )

    def test_parse_modules_discovers_client_upload_key(self) -> None:
        env = {
            'MEDIA_UPLOAD_HARD_MAX_SIZE': str(20 * 1024 * 1024 * 1024),
            'CLIENT_SAMPLE_UPLOAD_MAX_SIZE_MB': '800',
        }
        self.assertEqual(parse_modules_max_bytes(env), 800 * 1024 * 1024)

    def test_compute_body_hard_wins(self) -> None:
        env = {
            'MEDIA_UPLOAD_MAX_SIZE': str(524288000),
            'MEDIA_UPLOAD_HARD_MAX_SIZE': str(10 * 1024 * 1024 * 1024),
            'TASKS_MAX_ATTACHMENT_SIZE_MB': '600',
            'CLIENT_VIDEO_UPLOAD_MAX_SIZE_MB': '0',
            'NGINX_UPLOAD_BODY_MARGIN_PERCENT': '10',
        }
        body = compute_client_max_body_bytes(env)
        expected = int(10 * 1024 * 1024 * 1024 * 1.1)
        self.assertEqual(body, expected)

    def test_compute_body_tasks_when_hard_capped_low(self) -> None:
        # hard = default media 500; tasks 600 → 660 MiB
        env = {
            'MEDIA_UPLOAD_MAX_SIZE': str(524288000),
            'MEDIA_UPLOAD_HARD_MAX_SIZE': str(524288000),
            'TASKS_MAX_ATTACHMENT_SIZE_MB': '600',
            'CLIENT_VIDEO_UPLOAD_MAX_SIZE_MB': '100',
            'NGINX_UPLOAD_BODY_MARGIN_PERCENT': '10',
        }
        body = compute_client_max_body_bytes(env)
        expected = int(600 * 1024 * 1024 * 1.1)
        self.assertEqual(body, expected)
        self.assertEqual(format_nginx_body_size(body), '660m')

    def test_format_nginx_body_size_mib_ceil(self) -> None:
        self.assertEqual(format_nginx_body_size(1), '1m')
        self.assertEqual(format_nginx_body_size(1024 * 1024), '1m')
        self.assertEqual(format_nginx_body_size(1024 * 1024 + 1), '2m')

    def test_format_nginx_exact_gib(self) -> None:
        self.assertEqual(format_nginx_body_size(1024 * 1024 * 1024), '1g')


if __name__ == '__main__':
    unittest.main()
