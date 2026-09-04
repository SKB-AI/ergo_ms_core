from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from logs_paths import (  # noqa: E402
    is_known_service_log,
    parse_module_process_unit,
    resolve_service_log_files,
)


class LogsPathsModuleUnitTests(unittest.TestCase):
    def test_module_api_maps_to_api_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'logs').mkdir()
            paths = resolve_service_log_files('ergo_ms_module_demo_api', root)
            self.assertEqual([path.name for path in paths], ['api.log'])

    def test_module_worker_and_beat_map_to_celery_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                [path.name for path in resolve_service_log_files('ergo_ms_module_demo_worker', root)],
                ['celery_worker.log'],
            )
            self.assertEqual(
                [path.name for path in resolve_service_log_files('ergo_ms_module_demo_beat.service', root)],
                ['celery_beat.log'],
            )

    def test_known_log_accepts_module_unit_and_core_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(is_known_service_log('ergo_ms_module_demo_api', root))
            self.assertTrue(is_known_service_log('ergo_ms_api_dev', root))
            self.assertTrue(is_known_service_log('ergo_ms_celery_beat', root))
            self.assertFalse(is_known_service_log('not_a_real_service', root))

    def test_parse_accepts_any_service_prefix(self) -> None:
        self.assertEqual(parse_module_process_unit('ergo_st_logs_module_demo_api'), ('demo', 'api'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                [
                    path.name
                    for path in resolve_service_log_files('ergo_st_logs_module_demo_worker', root)
                ],
                ['celery_worker.log'],
            )


if __name__ == '__main__':
    unittest.main()
