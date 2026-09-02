from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from lifecycle.docker.ops import api_install_shell  # noqa: E402
from lifecycle.runner import _peel_project_root, detect_project_root  # noqa: E402
from system_test.environment import IsolatedEnvironment  # noqa: E402
from system_test.worktree_env import HostWorktreeEnvironment, link_host_dir  # noqa: E402


class RunnerProjectRootTests(unittest.TestCase):
    def test_peel_project_root_keeps_recipe(self) -> None:
        hint, rest = _peel_project_root(['docker-init', '--project-root', r'C:\tree'])
        self.assertEqual(hint, Path(r'C:\tree'))
        self.assertEqual(rest, ['docker-init'])

    def test_detect_project_root_honors_env(self) -> None:
        workspace = Path(__file__).resolve().parents[3]
        with patch.dict(os.environ, {'ERGOMS_PROJECT_ROOT': str(workspace)}, clear=False):
            self.assertEqual(detect_project_root(), workspace.resolve())


class ErgomsRootFlagTests(unittest.TestCase):
    def test_windows_invocation_passes_root(self) -> None:
        if os.name != 'nt':
            self.skipTest('флаг -Root проверяем на Windows')

        class Dummy(IsolatedEnvironment):
            kind = 'host'

            def provision(self) -> None:
                return

            def start(self) -> None:
                return

            def teardown(self) -> None:
                return

        workspace = Path(__file__).resolve().parents[3]
        env = Dummy(workspace, workspace / 'virtual_env' / 'cache' / 'tmp', 'ergo_st_test')
        env.tree_root = workspace
        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured['command'] = command
            captured['env'] = kwargs.get('env')

            class Result:
                returncode = 0
                stdout = ''
                stderr = ''

            return Result()

        with patch('system_test.environment.subprocess.run', fake_run):
            env.run_ergoms('help')
        command = captured['command']
        self.assertIn('-Root', command)
        self.assertIn(str(workspace), command)
        run_env = captured['env']
        self.assertEqual(run_env['ERGOMS_PROJECT_ROOT'], str(workspace))


class DockerApiShellTests(unittest.TestCase):
    def test_install_shell_does_not_use_poetry_run(self) -> None:
        shell = api_install_shell()
        self.assertIn('PYTHONPATH=/app/core/api:/app', shell)
        self.assertIn('/usr/local/bin/python -m commands install', shell)
        self.assertNotIn('poetry run', shell)


class OverlayDeploymentTests(unittest.TestCase):
    def test_overlay_copies_dirty_deployment_file(self) -> None:
        workspace = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            env = HostWorktreeEnvironment(workspace, run_dir, 'ergo_st_overlay')
            env.tree_root = run_dir / 'tree'
            target = env.tree_root / 'core' / 'deployment' / 'docker'
            target.mkdir(parents=True)
            (target / 'stale.txt').write_text('old', encoding='utf-8')
            env._overlay_workspace_deployment()
            self.assertTrue((env.tree_root / 'core' / 'deployment' / 'docker' / 'docker_cli.py').is_file())

    def test_link_checkouts_replaces_empty_api_dir(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / 'ws'
            api_src = workspace / 'core' / 'api' / 'commands'
            api_src.mkdir(parents=True)
            (api_src / '__main__.py').write_text('ok', encoding='utf-8')
            run_dir = root / 'run'
            env = HostWorktreeEnvironment(workspace, run_dir, 'ergo_st_api')
            env.tree_root = run_dir / 'tree'
            api_dir = env.tree_root / 'core' / 'api'
            api_dir.mkdir(parents=True)
            (api_dir / '.git').write_text('gitdir: fake', encoding='utf-8')
            env._link_workspace_checkouts()
            self.assertTrue((api_dir / 'commands' / '__main__.py').is_file())
            self.assertTrue((workspace / 'core' / 'api' / 'commands' / '__main__.py').is_file())


class LinkHostDirTests(unittest.TestCase):
    def test_replaces_gitkeep_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'source'
            source.mkdir()
            (source / 'marker.txt').write_text('ok', encoding='utf-8')
            target = root / 'virtual_env' / 'python'
            target.mkdir(parents=True)
            (target / '.gitkeep').write_text('', encoding='utf-8')
            link_host_dir(source, target)
            self.assertTrue((target / 'marker.txt').is_file())
            self.assertEqual((target / 'marker.txt').read_text(encoding='utf-8'), 'ok')


if __name__ == '__main__':
    unittest.main()
