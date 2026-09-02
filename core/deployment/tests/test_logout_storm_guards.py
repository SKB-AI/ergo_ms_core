from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from render_common import build_core_proxy_locations, build_logout_location
from validate_logout_storm_guards import find_logout_storm_guard_violations


class LogoutStormGuardTests(unittest.TestCase):
    def test_required_source_markers_present(self) -> None:
        missing = find_logout_storm_guard_violations()
        self.assertEqual(missing, [])

    def test_logout_location_answers_204_on_limit(self) -> None:
        block = build_logout_location(include_maintenance=True)
        self.assertIn('limit_req zone=ergo_logout', block)
        self.assertIn('error_page 429 =204 @logout_limited', block)
        self.assertIn('return 204;', block)
        self.assertNotIn('proxy_pass', block.split('location @logout_limited')[1])

    def test_exact_logout_location_wins_over_api_prefix(self) -> None:
        for variant in ('host', 'docker'):
            block = build_core_proxy_locations(variant=variant)
            logout_at = block.find('location = /api/cms/adp/logout/')
            api_at = block.find('location /api/')
            self.assertGreater(logout_at, -1, variant)
            self.assertGreater(api_at, -1, variant)
            self.assertLess(logout_at, api_at, variant)


if __name__ == '__main__':
    unittest.main()
