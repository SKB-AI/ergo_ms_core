"""Тесты контроля media.content_validation (security audit С5)."""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.checkers import _REGISTRY
from security.checkers.media_content_validation import run as content_validation_run
from security.profile_defaults import merge_security_profile_defaults


class MediaContentValidationCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('media.content_validation')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'media_content_validation')
        self.assertEqual(self.control.status, 'partial')
        self.assertEqual(self.control.env_key, 'MEDIA_API_CONTENT_VALIDATION')
        self.assertIn('media_content_validation', _REGISTRY)

    def _run(self, *, level: str, values: dict[str, str]):
        return content_validation_run(
            self.control,
            self.catalog,
            {'values': values, 'level': level, 'root': '.'},
        )

    def test_standard_ok_default(self) -> None:
        finding = self._run(level='standard', values={})
        self.assertEqual(finding.severity, 'ok')
        self.assertNotEqual(finding.severity, 'skip')

    def test_hardened_unset_ok_via_profile(self) -> None:
        finding = self._run(level='hardened', values={})
        self.assertEqual(finding.severity, 'ok')
        self.assertNotEqual(finding.severity, 'skip')

    def test_hardened_explicit_magic_ok(self) -> None:
        finding = self._run(
            level='hardened',
            values={'MEDIA_API_CONTENT_VALIDATION': 'extension_and_magic'},
        )
        self.assertEqual(finding.severity, 'ok')

    def test_standard_weaker_extension_error(self) -> None:
        finding = self._run(
            level='standard',
            values={'MEDIA_API_CONTENT_VALIDATION': 'extension'},
        )
        self.assertEqual(finding.severity, 'error')
        self.assertIn('extension', finding.message)

    def test_hardened_weaker_extension_error(self) -> None:
        finding = self._run(
            level='hardened',
            values={'MEDIA_API_CONTENT_VALIDATION': 'extension'},
        )
        self.assertEqual(finding.severity, 'error')
        self.assertIn('extension', finding.message)

    def test_maximum_without_scanner_skip(self) -> None:
        finding = self._run(
            level='maximum',
            values={'MEDIA_API_CONTENT_VALIDATION': 'extension_magic_av'},
        )
        self.assertEqual(finding.severity, 'skip')
        self.assertIn('AV', finding.message)

    def test_maximum_magic_only_warning(self) -> None:
        finding = self._run(
            level='maximum',
            values={'MEDIA_API_CONTENT_VALIDATION': 'extension_and_magic'},
        )
        self.assertEqual(finding.severity, 'warning')


class MediaContentValidationMergeTests(unittest.TestCase):
    def test_hardened_injects_magic(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'hardened'})
        self.assertEqual(merged['MEDIA_API_CONTENT_VALIDATION'], 'extension_and_magic')

    def test_standard_injects_magic(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'standard'})
        self.assertEqual(merged['MEDIA_API_CONTENT_VALIDATION'], 'extension_and_magic')

    def test_maximum_injects_av_mode(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'maximum'})
        self.assertEqual(merged['MEDIA_API_CONTENT_VALIDATION'], 'extension_magic_av')

    def test_explicit_kept(self) -> None:
        merged = merge_security_profile_defaults({
            'ERGO_SECURITY': 'hardened',
            'MEDIA_API_CONTENT_VALIDATION': 'extension',
        })
        self.assertEqual(merged['MEDIA_API_CONTENT_VALIDATION'], 'extension')


if __name__ == '__main__':
    unittest.main()
