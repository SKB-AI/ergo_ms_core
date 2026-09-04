from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from sync_vscode_logs_services import (  # noqa: E402
    _module_process_log_kinds,
    _shared_microservice_log_services,
    build_logs_all_services,
)


class SyncVscodeLogsMicroserviceTests(unittest.TestCase):
    def _write_module_host(self, root: Path, name: str, *, with_beat: bool) -> None:
        module_dir = root / 'modules' / name
        (module_dir / 'api').mkdir(parents=True)
        kinds = ['api', 'worker']
        if with_beat:
            kinds.append('beat')
        install = '\n'.join(
            f'    - install-module-service --module={name} --kind={kind}'
            for kind in kinds
        )
        uninstall = '\n'.join(
            f'    - uninstall-module-service --module={name} --kind={kind}'
            for kind in kinds
        )
        units = '\n'.join(f'    - ergo_ms_module_{name}_{kind}' for kind in kinds)
        (module_dir / 'host_lifecycle.yaml').write_text(
            f'module: {name}\n'
            'host:\n'
            '  install_service_commands:\n'
            f'{install}\n'
            '  uninstall_service_commands:\n'
            f'{uninstall}\n'
            '  service_units:\n'
            f'{units}\n',
            encoding='utf-8',
        )

    def _env(self, root: Path, *, profile: str, runtime: str, modules: str) -> None:
        (root / 'pyproject.toml').write_text('[project]\nname = "t"\n', encoding='utf-8')
        (root / '.env').write_text(
            f'HOST_PROFILE={profile}\n'
            f'MODULE_RUNTIME={runtime}\n'
            f'MICROSERVICE_MODULES={modules}\n'
            'ERGO_PROXY=none\n'
            'ERGO_BROKER=local\n'
            'ERGO_SEARCH_ENABLED=false\n',
            encoding='utf-8',
        )

    def test_microservice_shares_three_process_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._env(root, profile='modules', runtime='microservice', modules='demo_mod')
            self._write_module_host(root, 'demo_mod', with_beat=True)
            self.assertEqual(
                _module_process_log_kinds(root),
                {'api', 'worker', 'beat'},
            )
            items = _shared_microservice_log_services(root, with_commands=True)
            keys = [item['key'] for item in items]
            self.assertEqual(
                keys,
                [
                    'ergo_ms_api_dev',
                    'ergo_ms_celery_worker',
                    'ergo_ms_celery_beat',
                ],
            )
            self.assertTrue(all(item['command'].startswith('ergoms logs ') for item in items))
            self.assertEqual(
                [item['description'] for item in items],
                [
                    'Shared microservice API',
                    'Shared Celery Worker',
                    'Shared Celery Beat',
                ],
            )

    def test_monolith_skips_shared_microservice_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._env(root, profile='full', runtime='monolith', modules='')
            self._write_module_host(root, 'demo_mod', with_beat=True)
            self.assertEqual(_module_process_log_kinds(root), set())
            self.assertEqual(_shared_microservice_log_services(root), [])

    def test_modules_host_logs_all_uses_shared_keys_not_per_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._env(root, profile='modules', runtime='microservice', modules='demo_mod')
            self._write_module_host(root, 'demo_mod', with_beat=True)
            (root / 'celery_workers.yaml').write_text(
                'workers:\n  all:\n    queues: [default]\n',
                encoding='utf-8',
            )
            keys = [item['key'] for item in build_logs_all_services(root)]
            self.assertNotIn('ergo_ms_celery_worker_all', keys)
            self.assertNotIn('ergo_ms_module_demo_mod_api', keys)
            self.assertNotIn('ergo_ms_module_demo_mod_worker', keys)
            self.assertNotIn('ergo_ms_module_demo_mod_beat', keys)
            self.assertIn('ergo_ms_api_dev', keys)
            self.assertIn('ergo_ms_celery_worker', keys)
            self.assertIn('ergo_ms_celery_beat', keys)


if __name__ == '__main__':
    unittest.main()
