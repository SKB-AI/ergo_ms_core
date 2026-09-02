from __future__ import annotations

import re
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from command_conf import (  # noqa: E402
    LINUX_ONLY_WRAPPER_COMMANDS,
    PROXY_WRAPPER_COMMANDS,
    SHARED_WRAPPER_COMMANDS,
    extract_linux_case_commands,
    extract_windows_wrapper_commands,
    load_commands_conf,
    missing_platform_arms,
    split_composite_command,
)
from lifecycle.recipes import RECIPE_REGISTRY  # noqa: E402

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
_COMMANDS_CONF = _DEPLOYMENT_DIR / 'commands.conf'
_PS1 = _DEPLOYMENT_DIR / 'windows' / 'ergo_ms.ps1'
_SH = _DEPLOYMENT_DIR / 'linux' / 'ergo_ms.sh'
_HELP = _DEPLOYMENT_DIR / 'help.manifest.yaml'
_PS1_COMMANDS = _DEPLOYMENT_DIR / 'windows' / 'lib' / 'commands.ps1'
_SH_COMMANDS = _DEPLOYMENT_DIR / 'linux' / 'lib' / 'commands.sh'

_RECIPE_NAMES = (
    'nginx-install',
    'nginx-test',
    'service-install-all',
    'service-start-all',
    'docker-init',
    'docker-up',
    'docker-build',
)

_HOST_BUILTIN = (
    'install-nginx',
    'test-nginx',
    'start-nginx',
    'install-services',
    'start',
)


class CommandsCrossPlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.conf = load_commands_conf(_COMMANDS_CONF)
        cls.ps1 = _PS1.read_text(encoding='utf-8')
        cls.sh = _SH.read_text(encoding='utf-8')
        cls.help_text = _HELP.read_text(encoding='utf-8')
        cls.ps1_commands = _PS1_COMMANDS.read_text(encoding='utf-8')
        cls.sh_commands = _SH_COMMANDS.read_text(encoding='utf-8')

    def test_platform_prefixed_commands_have_both_arms(self) -> None:
        missing: dict[str, set[str]] = {}
        for name, body in self.conf.items():
            absent = missing_platform_arms(body)
            if absent:
                missing[name] = absent
        self.assertEqual(missing, {}, msg=f'commands.conf missing platform arms: {missing}')

    def test_split_does_not_cut_quoted_ampersand(self) -> None:
        body = (
            'win:echo one && linux:bash -c "python down && python up" && node check.js'
        )
        parts = split_composite_command(body)
        self.assertEqual(
            parts,
            [
                'win:echo one',
                'linux:bash -c "python down && python up"',
                'node check.js',
            ],
        )

    def test_lock_check_runs_node_once(self) -> None:
        parts = split_composite_command(self.conf['lock-check'])
        node_steps = [item for item in parts if item.startswith('node ')]
        self.assertEqual(len(node_steps), 1)

    def test_docker_restart_has_separate_linux_arms(self) -> None:
        parts = split_composite_command(self.conf['docker-restart'])
        linux_parts = [item for item in parts if item.startswith('linux:')]
        self.assertEqual(len(linux_parts), 2)
        self.assertTrue(all('bash -c' not in item for item in linux_parts))

    def test_shell_parsers_are_quote_aware(self) -> None:
        self.assertIn('function Split-CompositeCommand', self.ps1_commands)
        self.assertIn('_split_composite_command()', self.sh_commands)
        self.assertNotIn('${command_def// && /|}', self.sh_commands)
        self.assertNotIn("$commandDef -split '&&'", self.ps1_commands)

    def test_test_system_is_registered(self) -> None:
        self.assertIn('test_system', self.conf)
        self.assertIn('test-system', self.conf)
        self.assertIn('scripts/test_system.py', self.conf['test_system'].replace('\\', '/'))
        self.assertIn('name: test_system', self.help_text)

    def test_system_test_is_registered(self) -> None:
        self.assertIn('system-test', self.conf)
        self.assertIn('scripts/run_system_test.py', self.conf['system-test'].replace('\\', '/'))
        self.assertIn('name: system-test', self.help_text)

    def test_wrapper_builtins_match_on_both_os(self) -> None:
        windows = extract_windows_wrapper_commands(self.ps1)
        linux = extract_linux_case_commands(self.sh)
        self.assertTrue(windows)
        self.assertTrue(linux)
        missing_on_windows = sorted(SHARED_WRAPPER_COMMANDS - windows)
        missing_on_linux = sorted(SHARED_WRAPPER_COMMANDS - linux)
        self.assertEqual(missing_on_windows, [], msg=f'missing in ergo_ms.ps1: {missing_on_windows}')
        self.assertEqual(missing_on_linux, [], msg=f'missing in ergo_ms.sh: {missing_on_linux}')
        extra_linux = LINUX_ONLY_WRAPPER_COMMANDS - linux
        self.assertEqual(extra_linux, set(), msg=f'TLS commands missing in ergo_ms.sh: {extra_linux}')
        tls_on_windows = LINUX_ONLY_WRAPPER_COMMANDS & windows
        self.assertEqual(tls_on_windows, set(), msg='TLS install is Linux-only')
        self.assertTrue(PROXY_WRAPPER_COMMANDS <= (windows | linux))

    def test_help_names_resolve_to_conf_or_wrapper(self) -> None:
        help_names = set(re.findall(r'^\s+- name: ([a-zA-Z0-9_-]+)\s*$', self.help_text, re.M))
        known = set(self.conf) | SHARED_WRAPPER_COMMANDS | LINUX_ONLY_WRAPPER_COMMANDS | PROXY_WRAPPER_COMMANDS
        unknown = sorted(help_names - known)
        self.assertEqual(unknown, [], msg=f'help.manifest names not in commands.conf or wrappers: {unknown}')

    def test_lifecycle_recipes_registered(self) -> None:
        for name in _RECIPE_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, RECIPE_REGISTRY)

    def test_host_nginx_and_services_exist_on_both_os_wrappers(self) -> None:
        for name in _HOST_BUILTIN:
            with self.subTest(name=name):
                self.assertIn(name, self.ps1)
                self.assertIn(name, self.sh)

    def test_recipe_aliases_for_install_and_start(self) -> None:
        self.assertIs(RECIPE_REGISTRY['install-nginx'], RECIPE_REGISTRY['nginx-install'])
        self.assertIs(RECIPE_REGISTRY['install-services'], RECIPE_REGISTRY['service-install-all'])
        self.assertIs(RECIPE_REGISTRY['start'], RECIPE_REGISTRY['service-start-all'])
        self.assertIs(RECIPE_REGISTRY['test-nginx'], RECIPE_REGISTRY['nginx-test'])


if __name__ == '__main__':
    unittest.main()
