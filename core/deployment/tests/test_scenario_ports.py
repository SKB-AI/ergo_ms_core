from __future__ import annotations

import unittest
from unittest.mock import patch

import _bootstrap  # noqa: F401

from scenario_test.http_checks import extract_asset_paths, parse_ready_json, parse_wget_status  # noqa: E402
from scenario_test.ports import (  # noqa: E402
    API_CANDIDATES,
    pick_free_port,
    pick_scenario_ports,
)


class ScenarioPortPickerTests(unittest.TestCase):
    def test_pick_free_port_skips_busy(self) -> None:
        with patch('scenario_test.ports.host_tcp_port_available', side_effect=[False, True]):
            self.assertEqual(pick_free_port((18000, 18001)), 18001)

    def test_pick_free_port_none_when_all_busy(self) -> None:
        with patch('scenario_test.ports.host_tcp_port_available', return_value=False):
            self.assertIsNone(pick_free_port(API_CANDIDATES))

    def test_pick_scenario_ports_all_or_nothing(self) -> None:
        with patch('scenario_test.ports.host_tcp_port_available', return_value=True):
            ports = pick_scenario_ports()
        self.assertIsNotNone(ports)
        assert ports is not None
        self.assertEqual(
            set(ports),
            {'api', 'client', 'nginx', 'jupyter', 'postgres', 'redis', 'media', 'module', 'mysql', 'mssql'},
        )
        reserved = {80, 5432, 3306, 1433, 6379, 8000, 8001, 8002}
        self.assertTrue(reserved.isdisjoint(ports.values()))

    def test_extract_assets_and_ready_json(self) -> None:
        html = b'<html><script src="/assets/index-abc.js"></script></html>'
        self.assertEqual(extract_asset_paths(html), ['/assets/index-abc.js'])
        self.assertTrue(parse_ready_json(b'{"ready": true}'))
        self.assertFalse(parse_ready_json(b'{"ready": false}'))
        self.assertEqual(parse_wget_status('HTTP/1.1 403 Forbidden\n'), 403)
        self.assertEqual(parse_wget_status('wget: server returned error: HTTP/1.1 502 Bad Gateway\n'), 502)
        self.assertEqual(parse_wget_status('Connecting...\n'), 0)


if __name__ == '__main__':
    unittest.main()
