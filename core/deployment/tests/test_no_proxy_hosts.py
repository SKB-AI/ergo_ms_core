from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from no_proxy_hosts import (  # noqa: E402
    apply_effective_no_proxy_to_environ,
    collect_no_proxy_hosts,
)


class NoProxyHostsTests(unittest.TestCase):
    def test_includes_media_and_bridge_hosts(self):
        hosts = collect_no_proxy_hosts(
            {
                'NO_PROXY': 'core.internal',
                'BRIDGE_CORE_URL': 'http://10.0.0.8:8000',
                'MEDIA_API_ADVERTISE_URL': 'http://10.0.0.9:8003',
                'MEDIA_API_INTERNAL_URL': 'http://127.0.0.1:8003',
            }
        )
        self.assertIn('localhost', hosts)
        self.assertIn('core.internal', hosts)
        self.assertIn('10.0.0.8', hosts)
        self.assertIn('10.0.0.9', hosts)

    def test_apply_writes_both_keys(self):
        dest: dict[str, str] = {'NO_PROXY': 'core.internal'}
        csv = apply_effective_no_proxy_to_environ(dest)
        self.assertEqual(dest['NO_PROXY'], csv)
        self.assertEqual(dest['no_proxy'], csv)
        self.assertIn('core.internal', csv)
        self.assertIn('localhost', csv)


if __name__ == '__main__':
    unittest.main()
