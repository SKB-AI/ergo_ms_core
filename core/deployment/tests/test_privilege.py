from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from lifecycle.host.privilege import (  # noqa: E402
    CLIENT_BUILD_ARTIFACT_RELATIVE,
    RUNTIME_ARTIFACT_RELATIVE,
    drop_to_project_owner,
    project_owner_ids,
    restore_project_ownership,
)


class RuntimeArtifactOwnershipTests(unittest.TestCase):
    def test_client_build_and_logs_are_in_restore_lists(self) -> None:
        for relative in (
            'core/client/dist',
            'virtual_env/client-remotes',
            'virtual_env/cache',
            'logs',
        ):
            self.assertIn(relative, RUNTIME_ARTIFACT_RELATIVE)
            self.assertIn(relative, CLIENT_BUILD_ARTIFACT_RELATIVE)

    def test_restore_is_noop_when_owner_matches(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            logs = root / 'logs'
            logs.mkdir()
            (logs / 'api.log').write_text('ok', encoding='utf-8')
            self.assertTrue(restore_project_ownership(root, logs))
            uid, _gid = project_owner_ids(root)
            self.assertEqual((logs / 'api.log').stat().st_uid, uid)

    def test_drop_is_noop_when_not_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertFalse(drop_to_project_owner(Path(raw)))


if __name__ == '__main__':
    unittest.main()
