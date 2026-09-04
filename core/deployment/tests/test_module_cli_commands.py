from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from lifecycle.modules.cli_commands import (  # noqa: E402
    bind_cli_module_process_role,
    find_modules_owning_management_command,
    module_name_from_cli_command,
    module_process_role_for_cli_command,
)


class ModuleCliCommandsTests(unittest.TestCase):
    def test_module_name_from_prefixed_command(self) -> None:
        self.assertEqual(module_name_from_cli_command('demo_mod:sync-knowledge'), 'demo_mod')
        self.assertIsNone(module_name_from_cli_command('sync-knowledge'))
        self.assertIsNone(module_name_from_cli_command(':oops'))

    def test_process_role_only_for_api_defs(self) -> None:
        self.assertEqual(
            module_process_role_for_cli_command(
                'demo_mod:sync-knowledge',
                'api:sync_system_knowledge',
            ),
            'module:demo_mod',
        )
        self.assertEqual(
            module_process_role_for_cli_command(
                'demo_mod:install',
                'linux:echo ok && api:ensure_pgvector',
            ),
            'module:demo_mod',
        )
        self.assertIsNone(
            module_process_role_for_cli_command(
                'demo_mod:install-pkg',
                'linux:virtual_env/python/bin/python pkg.py',
            )
        )
        self.assertIsNone(
            module_process_role_for_cli_command('publish-knowledge-packs', 'api:publish_knowledge_packs')
        )

    def test_find_owner_skips_disabled_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_dir = root / 'demo_mod' / 'api' / 'management' / 'commands'
            cmd_dir.mkdir(parents=True)
            (cmd_dir / 'sync_demo.py').write_text('# command\n', encoding='utf-8')
            other = root / 'other_mod' / 'api' / 'management' / 'commands'
            other.mkdir(parents=True)
            (other / 'sync_demo.py').write_text('# command\n', encoding='utf-8')

            self.assertEqual(
                find_modules_owning_management_command('sync_demo', root),
                ['demo_mod', 'other_mod'],
            )
            self.assertEqual(
                find_modules_owning_management_command(
                    'sync_demo',
                    root,
                    disabled=('other_mod',),
                ),
                ['demo_mod'],
            )
            self.assertEqual(
                find_modules_owning_management_command('missing_cmd', root),
                [],
            )

    def test_bind_does_not_override_existing_role(self) -> None:
        previous = os.environ.get('ERGO_PROCESS_ROLE')
        os.environ['ERGO_PROCESS_ROLE'] = 'api'
        try:
            bind_cli_module_process_role('module:demo_mod')
            self.assertEqual(os.environ['ERGO_PROCESS_ROLE'], 'api')
        finally:
            if previous is None:
                os.environ.pop('ERGO_PROCESS_ROLE', None)
            else:
                os.environ['ERGO_PROCESS_ROLE'] = previous


if __name__ == '__main__':
    unittest.main()
