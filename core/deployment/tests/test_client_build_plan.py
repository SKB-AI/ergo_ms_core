from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from lifecycle.client_build_plan import (  # noqa: E402
    is_local_remote_entry,
    parse_client_module_remotes,
    resolve_client_build_plan,
    should_reload_nginx_after_client_build,
)
from lifecycle.host.ops import client_build_up_to_date, write_client_build_stamp  # noqa: E402


def _touch_module_client(root: Path, name: str) -> None:
    path = root / 'modules' / name / 'client'
    path.mkdir(parents=True, exist_ok=True)
    (path / '.keep').write_text('', encoding='utf-8')


class ParseClientModuleRemotesTests(unittest.TestCase):
    def test_parses_name_url_pairs(self) -> None:
        items = parse_client_module_remotes(
            'demo_mod=/remotes/demo_mod/remoteEntry.js, other=https://peer.example/r.js'
        )
        self.assertEqual(
            items,
            (
                ('demo_mod', '/remotes/demo_mod/remoteEntry.js'),
                ('other', 'https://peer.example/r.js'),
            ),
        )

    def test_skips_tokens_without_equals(self) -> None:
        self.assertEqual(parse_client_module_remotes('demo_mod'), ())


class LocalRemoteEntryTests(unittest.TestCase):
    def test_same_origin_remotes_path(self) -> None:
        self.assertTrue(is_local_remote_entry('/remotes/demo_mod/remoteEntry.js'))

    def test_http_is_not_local(self) -> None:
        self.assertFalse(is_local_remote_entry('https://peer.example/remotes/demo_mod/remoteEntry.js'))
        self.assertFalse(is_local_remote_entry('//peer.example/remotes/demo_mod/remoteEntry.js'))


class ClientBuildPlanTests(unittest.TestCase):
    def test_full_profile_builds_shell_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = resolve_client_build_plan(root, {'HOST_PROFILE': 'full'})
            self.assertTrue(plan.shell)
            self.assertEqual(plan.remotes, ())

    def test_modules_profile_skips_shell(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _touch_module_client(root, 'demo_mod')
            plan = resolve_client_build_plan(
                root,
                {
                    'HOST_PROFILE': 'modules',
                    'MICROSERVICE_MODULES': 'demo_mod',
                },
            )
            self.assertFalse(plan.shell)
            self.assertEqual(plan.remotes, ('demo_mod',))

    def test_modules_without_client_dir_skips_remote(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'modules' / 'demo_mod' / 'api').mkdir(parents=True)
            plan = resolve_client_build_plan(
                root,
                {
                    'HOST_PROFILE': 'modules',
                    'MICROSERVICE_MODULES': 'demo_mod',
                },
            )
            self.assertTrue(plan.is_empty())

    def test_core_skips_shell_when_client_upstream_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = resolve_client_build_plan(
                root,
                {
                    'HOST_PROFILE': 'core',
                    'NGINX_CLIENT_UPSTREAM': '10.0.0.8:80',
                },
            )
            self.assertFalse(plan.shell)
            self.assertTrue(plan.is_empty())

    def test_core_skips_local_remotes_when_remotes_upstream_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _touch_module_client(root, 'demo_mod')
            plan = resolve_client_build_plan(
                root,
                {
                    'HOST_PROFILE': 'core',
                    'CLIENT_MODULE_REMOTES': 'demo_mod=/remotes/demo_mod/remoteEntry.js',
                    'NGINX_CLIENT_REMOTES_UPSTREAM': '10.0.0.8:80',
                },
            )
            self.assertTrue(plan.shell)
            self.assertEqual(plan.remotes, ())

    def test_core_builds_local_remotes_without_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _touch_module_client(root, 'demo_mod')
            plan = resolve_client_build_plan(
                root,
                {
                    'HOST_PROFILE': 'core',
                    'CLIENT_MODULE_REMOTES': 'demo_mod=/remotes/demo_mod/remoteEntry.js',
                },
            )
            self.assertTrue(plan.shell)
            self.assertEqual(plan.remotes, ('demo_mod',))

    def test_only_modules_skips_shell(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = resolve_client_build_plan(
                root,
                {'HOST_PROFILE': 'full'},
                only_modules=('demo_mod',),
            )
            self.assertFalse(plan.shell)
            self.assertEqual(plan.remotes, ('demo_mod',))

    def test_disabled_module_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _touch_module_client(root, 'demo_mod')
            plan = resolve_client_build_plan(
                root,
                {
                    'HOST_PROFILE': 'modules',
                    'MICROSERVICE_MODULES': 'demo_mod',
                    'DISABLED_MODULES': 'demo_mod',
                },
            )
            self.assertTrue(plan.is_empty())

    def test_empty_modules_plan_is_up_to_date_without_dist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.assertTrue(
                client_build_up_to_date(root, {'HOST_PROFILE': 'modules'}),
            )

    def test_modules_remote_not_up_to_date_until_entry_exists(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _touch_module_client(root, 'demo_mod')
            env = {
                'HOST_PROFILE': 'modules',
                'MICROSERVICE_MODULES': 'demo_mod',
            }
            self.assertFalse(client_build_up_to_date(root, env))
            entry = root / 'virtual_env' / 'client-remotes' / 'demo_mod' / 'remoteEntry.js'
            entry.parent.mkdir(parents=True)
            entry.write_text('// remote\n', encoding='utf-8')
            write_client_build_stamp(root, env)
            self.assertTrue(client_build_up_to_date(root, env))


class NginxReloadAfterClientBuildTests(unittest.TestCase):
    def test_reload_when_proxy_is_nginx(self) -> None:
        self.assertTrue(should_reload_nginx_after_client_build({'ERGO_PROXY': 'nginx'}))

    def test_no_reload_when_proxy_is_none(self) -> None:
        self.assertFalse(should_reload_nginx_after_client_build({'ERGO_PROXY': 'none'}))

    def test_reload_when_legacy_nginx_enabled(self) -> None:
        self.assertTrue(
            should_reload_nginx_after_client_build({
                'NGINX_ENABLED': 'true',
                'ERGO_PROXY': 'none',
            }),
        )


if __name__ == '__main__':
    unittest.main()
