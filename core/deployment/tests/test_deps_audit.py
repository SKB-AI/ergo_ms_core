from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

from deps_audit import _cvss_v3_base_score, _osv_severity  # noqa: E402


class DepsAuditSeverityTests(unittest.TestCase):
    def test_cvss_vector_without_numeric_suffix_is_high(self) -> None:
        vector = 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H'
        self.assertGreaterEqual(_cvss_v3_base_score(vector), 7.0)
        label, score = _osv_severity({
            'severity': [{'type': 'CVSS_V3', 'score': vector}],
        })
        self.assertEqual(label, 'HIGH')
        self.assertGreaterEqual(score, 7.0)

    def test_database_specific_severity_high(self) -> None:
        label, _score = _osv_severity({'database_specific': {'severity': 'HIGH'}})
        self.assertEqual(label, 'HIGH')

    def test_cvss_v4_uses_database_specific_high(self) -> None:
        label, _score = _osv_severity({
            'severity': [{'type': 'CVSS_V4', 'score': 'CVSS:4.0/AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N'}],
            'database_specific': {'severity': 'HIGH'},
        })
        self.assertEqual(label, 'HIGH')

    def test_batch_stub_without_severity_stays_unknown(self) -> None:
        label, score = _osv_severity({'id': 'GHSA-0000-0000-0000'})
        self.assertEqual(label, 'UNKNOWN')
        self.assertEqual(score, 0.0)

    def test_numeric_score_still_parsed(self) -> None:
        label, score = _osv_severity({
            'severity': [{'type': 'CVSS_V3', 'score': '9.8'}],
        })
        self.assertEqual(label, 'CRITICAL')
        self.assertEqual(score, 9.8)


if __name__ == '__main__':
    unittest.main()
