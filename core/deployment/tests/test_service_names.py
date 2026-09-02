from __future__ import annotations

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from service_names import (  # noqa: E402
    API_DEV,
    DEFAULT_PREFIX,
    PREFIX_ENV,
    ServiceNames,
    module_service_name,
    normalize_service_name,
    resolve_service_prefix,
    sanitize_prefix,
)


class ServiceNamesTests(unittest.TestCase):
    def test_default_constants_keep_ergo_ms(self) -> None:
        self.assertEqual(API_DEV, 'ergo_ms_api_dev')
        self.assertEqual(ServiceNames().api_dev, 'ergo_ms_api_dev')

    def test_isolated_prefix_does_not_collide(self) -> None:
        names = ServiceNames('ergo_st_ab12')
        self.assertEqual(names.api_dev, 'ergo_st_ab12_api_dev')
        self.assertEqual(names.redis, 'ergo_st_ab12_redis')
        self.assertTrue(names.matches('ergo_st_ab12_api_dev.service'))
        self.assertFalse(names.matches('ergo_ms_api_dev'))

    def test_sanitize_rejects_unsafe_prefix(self) -> None:
        self.assertEqual(sanitize_prefix('../x'), DEFAULT_PREFIX)
        self.assertEqual(sanitize_prefix(''), DEFAULT_PREFIX)
        self.assertEqual(sanitize_prefix('ergo_st_1'), 'ergo_st_1')

    def test_resolve_reads_environ(self) -> None:
        self.assertEqual(
            resolve_service_prefix({PREFIX_ENV: 'ergo_st_zz'}),
            'ergo_st_zz',
        )

    def test_module_and_worker_names(self) -> None:
        names = ServiceNames('ergo_st_m')
        self.assertEqual(names.module('demo', 'api'), 'ergo_st_m_module_demo_api')
        self.assertEqual(names.celery_worker('heavy'), 'ergo_st_m_celery_worker_heavy')
        self.assertEqual(module_service_name('demo', 'beat', 'ergo_st_m'), 'ergo_st_m_module_demo_beat')

    def test_normalize_legacy_uses_current_prefix(self) -> None:
        self.assertEqual(normalize_service_name('ergo-api-dev'), 'ergo_ms_api_dev')
        self.assertEqual(
            normalize_service_name('ergo-api-dev', prefix='ergo_st_q'),
            'ergo_st_q_api_dev',
        )

    def test_cli_name_with_prefix_flag(self) -> None:
        from io import StringIO
        from unittest.mock import patch

        from service_names import _cli_main

        with patch('sys.argv', ['service_names.py', 'name', 'api_dev', '--prefix', 'ergo_st_cli']):
            with patch('sys.stdout', new_callable=StringIO) as out:
                code = _cli_main()
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), 'ergo_st_cli_api_dev')


class ServiceNameParityWithPrefixTests(unittest.TestCase):
    def test_helpers_exist_on_windows_and_linux(self) -> None:
        deployment = Path(__file__).resolve().parents[1]
        core_ps1 = (deployment / 'windows' / 'lib' / 'core.ps1').read_text(encoding='utf-8')
        core_sh = (deployment / 'linux' / 'lib' / 'core.sh').read_text(encoding='utf-8')
        self.assertIn('function Get-ErgoServicePrefix', core_ps1)
        self.assertIn('function Get-ErgoServiceName', core_ps1)
        self.assertIn('ergo_service_prefix()', core_sh)
        self.assertIn('ergo_service_name()', core_sh)

    def test_install_paths_use_prefix_helpers(self) -> None:
        deployment = Path(__file__).resolve().parents[1]
        services_sh = (deployment / 'linux' / 'lib' / 'services.sh').read_text(encoding='utf-8')
        self.assertIn('ergo_service_name api_dev', services_sh)
        nssm = (deployment / 'windows' / 'lib' / 'nssm.ps1').read_text(encoding='utf-8')
        self.assertIn('_api_dev$', nssm)

    def test_uninstall_legacy_only_for_default_prefix(self) -> None:
        deployment = Path(__file__).resolve().parents[1]
        services_ps1 = (deployment / 'windows' / 'lib' / 'services.ps1').read_text(encoding='utf-8')
        services_sh = (deployment / 'linux' / 'lib' / 'services.sh').read_text(encoding='utf-8')
        self.assertIn("if ($prefix -eq 'ergo_ms')", services_ps1)
        self.assertIn('[[ "$prefix" == "ergo_ms" ]]', services_sh)
        self.assertNotIn("Get-Service -Name 'ergo_ms_client_dev'", services_ps1)


if __name__ == '__main__':
    unittest.main()
