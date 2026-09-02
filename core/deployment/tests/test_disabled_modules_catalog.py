"""DISABLED_MODULES исключает модуль из loadable-каталога процесса."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from lifecycle.modules.catalog import ModuleCatalog  # noqa: E402


class DisabledModulesCatalogTests(unittest.TestCase):
    def _make_module(self, root: Path, name: str) -> None:
        (root / 'modules' / name / 'api').mkdir(parents=True)

    def test_disabled_module_not_loadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_module(root, 'sample_mod')
            catalog = ModuleCatalog.from_env(
                root,
                {'DISABLED_MODULES': 'sample_mod'},
            )
            self.assertTrue(catalog.is_disabled('sample_mod'))
            self.assertFalse(catalog.is_loadable_in_process('sample_mod'))
            self.assertNotIn('sample_mod', catalog.list_loadable_module_names())

    def test_enabled_module_is_loadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_module(root, 'sample_mod')
            catalog = ModuleCatalog.from_env(root, {})
            self.assertFalse(catalog.is_disabled('sample_mod'))
            self.assertTrue(catalog.is_loadable_in_process('sample_mod'))
            self.assertIn('sample_mod', catalog.list_loadable_module_names())


if __name__ == '__main__':
    unittest.main()
