from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from project_layout import npm_exe  # noqa: E402

TREE_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'sync-module-npm-tree.js'
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _run_tree_query(npm_root: Path, expression: str) -> str:
    node = npm_exe(PROJECT_ROOT).parent / 'node'
    if not node.is_file():
        node = Path('node')
    script = (
        'import { pathToFileURL } from "node:url";\n'
        f'const mod = await import(pathToFileURL({json.dumps(str(TREE_SCRIPT))}).href);\n'
        f'const npmRoot = {json.dumps(str(npm_root))};\n'
        'const nodeModules = npmRoot + "/node_modules";\n'
        f'{expression}\n'
    )
    result = subprocess.run(
        [str(node), '--input-type=module', '-e', script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


class SyncModuleNpmTreeTests(unittest.TestCase):
    def test_missing_core_specs_skip_installed_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            npm_root = Path(tmp)
            workspace = npm_root / 'client'
            workspace.mkdir()
            (workspace / 'package.json').write_text(
                json.dumps({'name': '@ergo-ms/core-client', 'version': '3.0.0'}),
                encoding='utf-8',
            )
            (npm_root / 'package.json').write_text(
                json.dumps({
                    'name': 'ergo-ms',
                    'workspaces': ['./client'],
                    'dependencies': {
                        'vue': '^3.5.41',
                        'docx-preview': '^0.3.7',
                        'pdfjs-dist': '^5.4.296',
                    },
                }),
                encoding='utf-8',
            )
            vue_dir = npm_root / 'node_modules' / 'vue'
            vue_dir.mkdir(parents=True)
            (vue_dir / 'package.json').write_text(
                json.dumps({'name': 'vue', 'version': '3.5.42'}),
                encoding='utf-8',
            )
            (npm_root / 'node_modules' / '@ergo-ms' / 'core-client').mkdir(parents=True)

            specs = json.loads(_run_tree_query(
                npm_root,
                'console.log(JSON.stringify(mod.collectMissingCoreInstallSpecs(npmRoot, nodeModules)))',
            ))
            self.assertEqual(
                sorted(specs),
                ['docx-preview@^0.3.7', 'pdfjs-dist@^5.4.296'],
            )

    def test_core_tree_current_is_false_when_only_stamp_drifts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            npm_root = Path(tmp)
            (npm_root / 'package.json').write_text(
                json.dumps({'name': 'ergo-ms', 'dependencies': {'vue': '^3.5.41'}}),
                encoding='utf-8',
            )
            (npm_root / 'package-lock.json').write_text('{"lockfileVersion":3}\n', encoding='utf-8')
            vue_dir = npm_root / 'node_modules' / 'vue'
            vue_dir.mkdir(parents=True)
            (vue_dir / 'package.json').write_text(
                json.dumps({'name': 'vue', 'version': '3.5.42'}),
                encoding='utf-8',
            )
            (npm_root / 'node_modules' / '.ergo-core-tree-ok').write_text('stale\n', encoding='utf-8')

            current = _run_tree_query(
                npm_root,
                'console.log(String(mod.isCoreTreeCurrent({ npmRoot, nodeModules })))',
            )
            missing = json.loads(_run_tree_query(
                npm_root,
                'console.log(JSON.stringify(mod.collectMissingCoreInstallSpecs(npmRoot, nodeModules)))',
            ))
            self.assertEqual(current, 'false')
            self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
