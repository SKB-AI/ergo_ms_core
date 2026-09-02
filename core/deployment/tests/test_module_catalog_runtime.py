from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from lifecycle.modules.catalog import (  # noqa: E402
    ModuleCatalog,
    parse_module_runtime,
)


class ModuleCatalogRuntimeTests(unittest.TestCase):
    def test_split_alias_is_microservice(self) -> None:
        self.assertEqual(parse_module_runtime('split'), 'microservice')
        self.assertEqual(parse_module_runtime('microservice'), 'microservice')
        self.assertEqual(parse_module_runtime('monolith'), 'monolith')
        self.assertEqual(parse_module_runtime('unknown'), 'monolith')

    def test_core_api_excludes_microservice_modules(self) -> None:
        catalog = ModuleCatalog(
            project_root=self._empty_root(),
            module_runtime='microservice',
            process_role='api',
            microservice_modules=frozenset({'demo_mod'}),
        )
        self.assertFalse(catalog.is_loadable_in_process('demo_mod'))
        self.assertTrue(catalog.is_loadable_in_process('other_mod'))

    def test_core_beat_excludes_microservice_modules(self) -> None:
        catalog = ModuleCatalog(
            project_root=self._empty_root(),
            module_runtime='microservice',
            process_role='beat',
            microservice_modules=frozenset({'demo_mod'}),
        )
        self.assertFalse(catalog.is_loadable_in_process('demo_mod'))
        self.assertTrue(catalog.is_loadable_in_process('other_mod'))

    def test_module_process_loads_only_own_name(self) -> None:
        catalog = ModuleCatalog(
            project_root=self._empty_root(),
            module_runtime='microservice',
            process_role='module:demo_mod',
            microservice_modules=frozenset({'demo_mod'}),
        )
        self.assertTrue(catalog.is_loadable_in_process('demo_mod'))
        self.assertFalse(catalog.is_loadable_in_process('other_mod'))

    def test_monolith_api_loads_all_non_disabled(self) -> None:
        catalog = ModuleCatalog(
            project_root=self._empty_root(),
            module_runtime='monolith',
            process_role='api',
            disabled=frozenset({'gone'}),
            microservice_modules=frozenset({'demo_mod'}),
        )
        self.assertTrue(catalog.is_loadable_in_process('demo_mod'))
        self.assertFalse(catalog.is_loadable_in_process('gone'))

    def test_disabled_never_loadable(self) -> None:
        catalog = ModuleCatalog(
            project_root=self._empty_root(),
            module_runtime='monolith',
            process_role='api',
            disabled=frozenset({'demo_mod'}),
        )
        self.assertFalse(catalog.is_loadable_in_process('demo_mod'))

    def _empty_root(self):
        from pathlib import Path

        return Path(__file__).resolve().parents[3]


if __name__ == '__main__':
    unittest.main()
