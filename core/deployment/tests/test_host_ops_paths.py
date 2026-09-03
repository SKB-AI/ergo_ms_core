from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from lifecycle.context import HostPlatform  # noqa: E402
from lifecycle.host.ops import (  # noqa: E402
    find_npm,
    path_separator,
    venv_dir,
    venv_python_exe,
)
from project_layout import (  # noqa: E402
    backups_dir,
    cache_tmp_dir,
    client_cli_path_dirs,
    huggingface_snapshot_dir,
    npm_bin_dir,
    npm_exe,
    npm_root_dir,
    portable_python_exe,
    prepend_client_cli_path,
    tool_cache_environ,
    virtual_env_dir,
    wrappers_dir,
)


class HostOpsPathTests(unittest.TestCase):
    def test_venv_python_layout_per_platform(self) -> None:
        root = Path('C:/ergo') if HostPlatform.current() == HostPlatform.WIN32 else Path('/opt/ergo')
        win_py = venv_python_exe(root, HostPlatform.WIN32)
        linux_py = venv_python_exe(root, HostPlatform.LINUX)
        darwin_py = venv_python_exe(root, HostPlatform.DARWIN)
        self.assertEqual(win_py, venv_dir(root) / 'Scripts' / 'python.exe')
        self.assertEqual(linux_py, venv_dir(root) / 'bin' / 'python')
        self.assertEqual(darwin_py, venv_dir(root) / 'bin' / 'python')
        self.assertNotEqual(win_py, linux_py)

    def test_path_separator_per_platform(self) -> None:
        self.assertEqual(path_separator(HostPlatform.WIN32), ';')
        self.assertEqual(path_separator(HostPlatform.LINUX), ':')
        self.assertEqual(path_separator(HostPlatform.DARWIN), ':')

    def test_find_npm_prefers_portable_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portable = npm_exe(root)
            portable.parent.mkdir(parents=True, exist_ok=True)
            portable.write_text('', encoding='utf-8')
            found = find_npm(root, HostPlatform.current())
            self.assertEqual(found, str(portable))

    def test_project_layout_stays_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            root.mkdir()
            paths = (
                virtual_env_dir(root),
                npm_root_dir(root),
                cache_tmp_dir(root),
                wrappers_dir(root),
                backups_dir(root),
                portable_python_exe(root),
                huggingface_snapshot_dir(root, '../escape/name'),
            )
            root_resolved = str(root.resolve())
            for path in paths:
                self.assertTrue(
                    str(path.resolve()).startswith(root_resolved),
                    msg=f'{path} left project root',
                )
            snapshot = huggingface_snapshot_dir(root, '../escape/name')
            self.assertEqual(snapshot.name, 'name')
            self.assertNotIn('..', snapshot.parts)

    def test_tool_cache_environ_uses_virtual_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = tool_cache_environ(root)
            cache = (root / 'virtual_env' / 'cache').resolve()
            for key in ('PIP_CACHE_DIR', 'POETRY_CACHE_DIR', 'npm_config_cache', 'NPM_CONFIG_CACHE'):
                self.assertIn(key, env)
                value = Path(env[key]).resolve()
                self.assertTrue(str(value).startswith(str(cache)), msg=key)

    def test_prepend_client_cli_path_includes_npm_bin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            npm_bin = npm_bin_dir(root)
            npm_bin.mkdir(parents=True)
            env = {'PATH': '/usr/bin'}
            prepend_client_cli_path(env, root)
            self.assertIn(str(npm_bin), env['PATH'].split(path_separator()))
            self.assertEqual(client_cli_path_dirs(root), [npm_bin])


if __name__ == '__main__':
    unittest.main()
