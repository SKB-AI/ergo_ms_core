from __future__ import annotations

import sys
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

_API_DIR = Path(__file__).resolve().parents[2] / 'api'
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from src.core.utils.celery.module_queues import queues_for_module  # noqa: E402


class ModuleQueuesTests(unittest.TestCase):
    def test_includes_nested_app_queues(self) -> None:
        routes = {
            'modules.demo_mod.api.tasks.*': {'queue': 'demo_mod'},
            'modules.demo_mod.api.source.tasks.*': {'queue': 'source_q'},
            'modules.other.api.tasks.*': {'queue': 'other'},
        }
        missing = Path(self.id().replace('.', '_'))
        self.assertEqual(
            queues_for_module('demo_mod', routes=routes, module_dir=missing),
            ['demo_mod', 'source_q'],
        )

    def test_empty_name(self) -> None:
        self.assertEqual(queues_for_module(''), [])

    def test_catalog_name_always_included(self) -> None:
        routes = {
            'modules.demo_mod.api.source.tasks.*': {'queue': 'source_q'},
        }
        missing = Path(self.id().replace('.', '_'))
        self.assertEqual(
            queues_for_module('demo_mod', routes=routes, module_dir=missing),
            ['demo_mod', 'source_q'],
        )

    def test_reads_nested_celery_config_when_routes_empty(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            nested = root / 'roadmap_sh'
            nested.mkdir()
            (nested / 'celery_config.py').write_text(
                "class Cfg:\n"
                "    def get_task_routes(self):\n"
                "        return {'modules.demo_mod.api.roadmap_sh.tasks.*': {'queue': 'roadmap_sh'}}\n",
                encoding='utf-8',
            )
            self.assertEqual(
                queues_for_module('demo_mod', routes={}, module_dir=root),
                ['demo_mod', 'roadmap_sh'],
            )


if __name__ == '__main__':
    unittest.main()
