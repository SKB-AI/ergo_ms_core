const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function resolveProjectPython(repoRoot) {
    const candidates = process.platform === 'win32'
        ? [
            path.join(repoRoot, 'virtual_env', 'python', 'Scripts', 'python.exe'),
            path.join(repoRoot, 'virtual_env', 'packages', 'python', 'python.exe'),
        ]
        : [
            path.join(repoRoot, 'virtual_env', 'python', 'bin', 'python'),
            path.join(repoRoot, 'virtual_env', 'packages', 'python', 'python'),
        ];
    return candidates.find((candidate) => fs.existsSync(candidate)) || '';
}

/**
 * Writes Cursor state.vscdb keys that disable Browser Tab auto-open.
 * @param {import('vscode').Uri | undefined} workspaceFolderUri
 * @returns {{ changed: boolean, skipped: boolean, error?: string }}
 */
function applyCursorBrowserPolicy(workspaceFolderUri) {
    if (!workspaceFolderUri || workspaceFolderUri.scheme !== 'file') {
        return { changed: false, skipped: true };
    }

    const repoRoot = workspaceFolderUri.fsPath;
    const pythonExe = resolveProjectPython(repoRoot);
    const scriptPath = path.join(
        repoRoot,
        'core',
        'deployment',
        'scripts',
        'cursor_ide_browser_policy.py',
    );
    if (!pythonExe || !fs.existsSync(scriptPath)) {
        return { changed: false, skipped: true };
    }

    const result = spawnSync(pythonExe, [scriptPath, '--json'], {
        cwd: repoRoot,
        encoding: 'utf8',
        windowsHide: true,
    });
    if (result.status !== 0) {
        const detail = (result.stderr || result.stdout || '').trim();
        return { changed: false, skipped: false, error: detail || `exit ${result.status}` };
    }

    try {
        const payload = JSON.parse((result.stdout || '').trim() || '{}');
        const changed = Array.isArray(payload.changed) && payload.changed.length > 0;
        return { changed, skipped: Boolean(payload.skipped) };
    } catch (error) {
        return { changed: false, skipped: false, error: error.message };
    }
}

module.exports = {
    applyCursorBrowserPolicy,
};
