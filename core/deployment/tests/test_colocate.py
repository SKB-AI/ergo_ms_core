from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from lifecycle.modules.colocate import (  # noqa: E402
    colocated_module_names,
    colocated_module_names_from_env,
    is_colocated_url,
    is_loopback_host,
    parse_bridge_colocate,
    parse_service_urls,
    this_process_hosts,
    url_host,
)


class ColocateTests(unittest.TestCase):
    def test_parse_urls_and_hosts(self) -> None:
        urls = parse_service_urls(
            'demo_mod=http://127.0.0.1:8123,peer=http://10.0.0.8:8000'
        )
        self.assertEqual(urls['demo_mod'], 'http://127.0.0.1:8123')
        self.assertEqual(url_host(urls['peer']), '10.0.0.8')
        self.assertTrue(is_loopback_host('127.0.0.1'))
        self.assertTrue(is_loopback_host('localhost'))
        self.assertFalse(is_loopback_host('10.0.0.8'))

    def test_loopback_is_colocated_remote_is_not(self) -> None:
        hosts = this_process_hosts({'API_HOST': '0.0.0.0'})
        self.assertTrue(is_colocated_url('http://127.0.0.1:8123', self_hosts=hosts))
        self.assertFalse(is_colocated_url('http://10.0.0.8:8000', self_hosts=hosts))

    def test_same_lan_host_is_colocated(self) -> None:
        hosts = this_process_hosts({'API_HOST': '10.0.0.5'})
        self.assertTrue(is_colocated_url('http://10.0.0.5:8124', self_hosts=hosts))
        self.assertFalse(is_colocated_url('http://10.0.0.8:8000', self_hosts=hosts))

    def test_microservice_without_url_counts_local(self) -> None:
        names = colocated_module_names(
            service_urls={'peer': 'http://10.0.0.8:8000'},
            microservice_modules=frozenset({'demo_mod'}),
            self_hosts=this_process_hosts({}),
        )
        self.assertIn('demo_mod', names)
        self.assertNotIn('peer', names)

    def test_auto_on_for_http_off_for_local(self) -> None:
        self.assertEqual(parse_bridge_colocate('auto', transport='http'), 'on')
        self.assertEqual(parse_bridge_colocate('', transport='local'), 'off')
        self.assertEqual(parse_bridge_colocate('off', transport='http'), 'off')

    def test_from_env_http_picks_loopback_only(self) -> None:
        names = colocated_module_names_from_env(
            {
                'BRIDGE_TRANSPORT': 'http',
                'BRIDGE_COLOCATE': 'auto',
                'MICROSERVICE_MODULES': 'demo_mod',
                'BRIDGE_SERVICE_URLS': (
                    'demo_mod=http://127.0.0.1:8123,peer=http://10.0.0.8:8000'
                ),
                'API_HOST': '0.0.0.0',
            }
        )
        self.assertEqual(names, frozenset({'demo_mod'}))

    def test_from_env_off_is_empty(self) -> None:
        names = colocated_module_names_from_env(
            {
                'BRIDGE_TRANSPORT': 'http',
                'BRIDGE_COLOCATE': 'off',
                'MICROSERVICE_MODULES': 'demo_mod',
                'BRIDGE_SERVICE_URLS': 'demo_mod=http://127.0.0.1:8123',
            }
        )
        self.assertEqual(names, frozenset())


if __name__ == '__main__':
    unittest.main()
