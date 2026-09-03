from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from lifecycle.module_process_profile import (  # noqa: E402
    PLATFORM_CORE_APPS,
    allow_core_url_route,
    extra_core_apps,
    filter_core_apps,
    is_slim_module_process,
)


class ModuleProcessProfileTests(unittest.TestCase):
    def test_full_does_not_filter(self) -> None:
        env = {'ERGO_PROCESS_ROLE': 'module:demo', 'MODULE_PROCESS_PROFILE': 'full'}
        self.assertFalse(is_slim_module_process(env))
        apps = ['src.core.audit', 'src.core.search', 'src.core.messenger']
        self.assertEqual(filter_core_apps(apps, None, env), apps)

    def test_slim_keeps_platform_drops_optional(self) -> None:
        env = {'ERGO_PROCESS_ROLE': 'module:demo', 'MODULE_PROCESS_PROFILE': 'slim'}
        self.assertTrue(is_slim_module_process(env))
        apps = ['src.core.audit', 'src.core.cms', 'src.core.search', 'src.core.messenger']
        kept = filter_core_apps(apps, None, env)
        self.assertIn('src.core.audit', kept)
        self.assertIn('src.core.cms', kept)
        self.assertNotIn('src.core.search', kept)
        self.assertNotIn('src.core.messenger', kept)

    def test_slim_extra_from_env(self) -> None:
        env = {
            'ERGO_PROCESS_ROLE': 'module:demo',
            'MODULE_PROCESS_PROFILE': 'slim',
            'MODULE_PROCESS_CORE_EXTRA': 'src.core.search',
        }
        extras = extra_core_apps(None, env)
        self.assertIn('src.core.search', extras)
        kept = filter_core_apps(
            ['src.core.audit', 'src.core.search'],
            None,
            env,
        )
        self.assertIn('src.core.search', kept)

    def test_slim_urls(self) -> None:
        env = {'ERGO_PROCESS_ROLE': 'module:demo', 'MODULE_PROCESS_PROFILE': 'slim'}
        self.assertTrue(allow_core_url_route('system/', env))
        self.assertTrue(allow_core_url_route('system/ready/', env))
        self.assertFalse(allow_core_url_route('cms/adp/', env))
        self.assertFalse(allow_core_url_route('client_monitor/', env))

    def test_core_api_process_not_slim(self) -> None:
        env = {'ERGO_PROCESS_ROLE': 'api', 'MODULE_PROCESS_PROFILE': 'slim'}
        self.assertFalse(is_slim_module_process(env))

    def test_modules_host_defaults_to_slim(self) -> None:
        env = {'ERGO_PROCESS_ROLE': 'module:demo', 'HOST_PROFILE': 'modules'}
        self.assertTrue(is_slim_module_process(env))

    def test_explicit_full_wins_on_modules_host(self) -> None:
        env = {
            'ERGO_PROCESS_ROLE': 'module:demo',
            'HOST_PROFILE': 'modules',
            'MODULE_PROCESS_PROFILE': 'full',
        }
        self.assertFalse(is_slim_module_process(env))

    def test_api_role_not_slim_on_modules_host(self) -> None:
        env = {'ERGO_PROCESS_ROLE': 'api', 'HOST_PROFILE': 'modules'}
        self.assertFalse(is_slim_module_process(env))

    def test_module_role_stays_full_without_host_profile(self) -> None:
        env = {'ERGO_PROCESS_ROLE': 'module:demo'}
        self.assertFalse(is_slim_module_process(env))

    def test_host_services_api_keeps_full_default(self) -> None:
        env = {
            'ERGO_PROCESS_ROLE': 'module:demo',
            'HOST_PROFILE': 'modules',
            'HOST_SERVICES': 'api',
        }
        self.assertFalse(is_slim_module_process(env))

    def test_platform_minimum(self) -> None:
        self.assertIn('src.core.integrations', PLATFORM_CORE_APPS)
        self.assertIn('src.core.cms.adp', PLATFORM_CORE_APPS)

    def test_hook_yaml_extras(self) -> None:
        env = {'ERGO_PROCESS_ROLE': 'module:demo_mod', 'MODULE_PROCESS_PROFILE': 'slim'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hook = root / 'modules' / 'demo_mod' / 'api'
            hook.mkdir(parents=True)
            (hook / 'process_profile.yaml').write_text(
                'core_apps:\n  - src.core.search\n',
                encoding='utf-8',
            )
            extras = extra_core_apps(root, env, 'demo_mod')
            self.assertIn('src.core.search', extras)
            kept = filter_core_apps(['src.core.audit', 'src.core.search'], root, env)
            self.assertIn('src.core.search', kept)


if __name__ == '__main__':
    unittest.main()
