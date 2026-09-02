from __future__ import annotations

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from system_test.catalog import (  # noqa: E402
    SUITE_ALL,
    SUITE_INSTALL,
    SUITE_SCENARIOS,
    cases_for_suite,
)
from system_test.cases.scenarios import AGENT_SPECS  # noqa: E402
from system_test.docker_env import DockerEnvironment  # noqa: E402
from system_test.os_services_env import OsServicesEnvironment  # noqa: E402
from system_test.suite import SystemSuite  # noqa: E402
from system_test.worktree_env import HostWorktreeEnvironment  # noqa: E402


class SystemTestCatalogTests(unittest.TestCase):
    def test_install_suite_has_from_scratch_case(self) -> None:
        names = [case.name for case in cases_for_suite(SUITE_INSTALL)]
        self.assertEqual(names, ['install_from_scratch'])

    def test_all_suite_covers_domains(self) -> None:
        domains = {case.domain for case in cases_for_suite(SUITE_ALL)}
        self.assertTrue(
            {'install', 'commands', 'security', 'performance', 'browser', 'os-services', 'scenarios'}
            <= domains
        )

    def test_scenarios_default_is_agent_set(self) -> None:
        names = [case.name for case in cases_for_suite(SUITE_SCENARIOS)]
        self.assertEqual(names, [f'scenario_{item}' for item in AGENT_SPECS])

    def test_host_env_uses_test_prefix(self) -> None:
        workspace = Path(__file__).resolve().parents[3]
        env = SystemSuite(workspace)._build_env('host', SUITE_INSTALL)
        self.assertIsInstance(env, HostWorktreeEnvironment)
        self.assertTrue(env.prefix.startswith('ergo_st_'))
        self.assertNotEqual(env.prefix, 'ergo_ms')

    def test_cli_does_not_shadow_package(self) -> None:
        deployment = Path(__file__).resolve().parents[1]
        self.assertFalse((deployment / 'scripts' / 'system_test.py').exists())
        self.assertTrue((deployment / 'scripts' / 'run_system_test.py').is_file())
        self.assertTrue((deployment / 'system_test' / '__init__.py').is_file())

    def test_docker_and_os_factories(self) -> None:
        workspace = Path(__file__).resolve().parents[3]
        suite = SystemSuite(workspace)
        docker = suite._build_env('docker', SUITE_INSTALL)
        os_env = suite._build_env('os-services', 'os-services')
        self.assertIsInstance(docker, DockerEnvironment)
        self.assertIsInstance(os_env, OsServicesEnvironment)
        self.assertTrue(docker.prefix.startswith('ergo_st_'))
        self.assertTrue(os_env.prefix.startswith('ergo_st_'))


if __name__ == '__main__':
    unittest.main()
