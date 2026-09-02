"""Тесты контроля api.object_permissions и ObjectPermissionMixin (С8 phase 1)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.checkers import _REGISTRY
from security.checkers.object_permissions import (
    find_object_permission_mixin,
    find_raw_model_viewsets,
    run as object_permissions_run,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIXIN_FILE = (
    _REPO_ROOT
    / 'core'
    / 'api'
    / 'src'
    / 'core'
    / 'utils'
    / 'permissions'
    / 'object_permissions.py'
)


def _load_mixin_module():
    """Импорт без Django: только файл object_permissions.py."""
    spec = importlib.util.spec_from_file_location(
        'ergo_object_permissions_ut',
        _MIXIN_FILE,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ObjectPermissionMixinUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_mixin_module()

    def test_mixin_default_denies(self) -> None:
        mixin = self.mod.ObjectPermissionMixin()
        self.assertFalse(mixin.check_object_permission(None, object()))
        self.assertFalse(mixin.has_object_permission(None, None, object()))

    def test_filter_queryset_empty_when_none(self) -> None:
        class DummyQS:
            def none(self):
                return 'empty'

        self.assertEqual(
            self.mod.filter_queryset_for_user(DummyQS(), user=None),
            'empty',
        )
        sentinel = object()
        self.assertIs(
            self.mod.filter_queryset_for_user(sentinel, user=None),
            sentinel,
        )

    def test_override_denies(self) -> None:
        class Deny(self.mod.ObjectPermissionMixin):
            def check_object_permission(self, request, obj):
                return False

        self.assertFalse(Deny().has_object_permission(None, None, object()))


class ObjectPermissionsCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('api.object_permissions')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'object_permissions')
        self.assertEqual(self.control.status, 'partial')
        self.assertIn('object_permissions', _REGISTRY)

    def _run(self, *, level: str, root: Path | str = _REPO_ROOT):
        return object_permissions_run(
            self.control,
            self.catalog,
            {'values': {}, 'level': level, 'root': str(root)},
        )

    def test_standard_ok(self) -> None:
        finding = self._run(level='standard')
        self.assertEqual(finding.severity, 'ok')

    def test_open_ok(self) -> None:
        finding = self._run(level='open')
        self.assertEqual(finding.severity, 'ok')

    def test_hardened_ok_when_no_raw_viewsets(self) -> None:
        finding = self._run(level='hardened')
        self.assertEqual(finding.severity, 'ok')
        self.assertIn('сырых ModelViewSet', finding.message)

    def test_maximum_ok_when_no_raw_viewsets(self) -> None:
        finding = self._run(level='maximum')
        self.assertEqual(finding.severity, 'ok')

    def test_hardened_error_without_mixin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'core' / 'api' / 'src' / 'core').mkdir(parents=True)
            finding = self._run(level='hardened', root=root)
            self.assertEqual(finding.severity, 'error')
            self.assertIn('ObjectPermissionMixin', finding.message)

    def test_find_mixin_in_repo(self) -> None:
        found = find_object_permission_mixin(_REPO_ROOT / 'core' / 'api' / 'src')
        self.assertIsNotNone(found)
        self.assertEqual(found.name, 'object_permissions.py')

    def test_repo_has_no_raw_viewsets(self) -> None:
        raw = find_raw_model_viewsets(_REPO_ROOT / 'core' / 'api' / 'src')
        self.assertEqual(raw, [])


if __name__ == '__main__':
    unittest.main()
