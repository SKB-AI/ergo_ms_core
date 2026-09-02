"""Тесты контроля adp.default_role_view_grants (security audit С7)."""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.checkers import _REGISTRY
from security.checkers.adp_default_role_view_grants import run as view_grants_run
from security.profile_defaults import merge_security_profile_defaults


class AdpDefaultRoleViewGrantsCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('adp.default_role_view_grants')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'adp_default_role_view_grants')
        self.assertEqual(self.control.status, 'implemented')
        self.assertEqual(self.control.env_key, 'API_ADP_DEFAULT_VIEW_GRANTS')
        self.assertIn('adp_default_role_view_grants', _REGISTRY)

    def _run(self, *, level: str, values: dict[str, str]):
        return view_grants_run(
            self.control,
            self.catalog,
            {'values': values, 'level': level, 'root': '.'},
        )

    def test_standard_ok_default(self) -> None:
        finding = self._run(level='standard', values={})
        self.assertEqual(finding.severity, 'ok')
        self.assertIn('granted', finding.message)

    def test_hardened_unset_ok_via_profile(self) -> None:
        finding = self._run(level='hardened', values={})
        self.assertEqual(finding.severity, 'ok')
        self.assertIn('denied', finding.message)

    def test_hardened_explicit_denied_ok(self) -> None:
        finding = self._run(
            level='hardened',
            values={'API_ADP_DEFAULT_VIEW_GRANTS': 'denied'},
        )
        self.assertEqual(finding.severity, 'ok')

    def test_hardened_weaker_granted_error(self) -> None:
        finding = self._run(
            level='hardened',
            values={'API_ADP_DEFAULT_VIEW_GRANTS': 'granted'},
        )
        self.assertEqual(finding.severity, 'error')
        self.assertIn('granted', finding.message)

    def test_open_granted_ok(self) -> None:
        finding = self._run(level='open', values={})
        self.assertEqual(finding.severity, 'ok')

    def test_maximum_denied_ok(self) -> None:
        finding = self._run(
            level='maximum',
            values={'API_ADP_DEFAULT_VIEW_GRANTS': 'denied'},
        )
        self.assertEqual(finding.severity, 'ok')


class AdpDefaultRoleViewGrantsMergeTests(unittest.TestCase):
    def test_standard_injects_granted(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'standard'})
        self.assertEqual(merged['API_ADP_DEFAULT_VIEW_GRANTS'], 'granted')

    def test_hardened_injects_denied(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'hardened'})
        self.assertEqual(merged['API_ADP_DEFAULT_VIEW_GRANTS'], 'denied')

    def test_explicit_kept(self) -> None:
        merged = merge_security_profile_defaults({
            'ERGO_SECURITY': 'hardened',
            'API_ADP_DEFAULT_VIEW_GRANTS': 'granted',
        })
        self.assertEqual(merged['API_ADP_DEFAULT_VIEW_GRANTS'], 'granted')


if __name__ == '__main__':
    unittest.main()
