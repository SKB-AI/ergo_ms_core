const vscode = require('vscode');
const fs = require('fs');
const path = require('path');
const os = require('os');
const osAbstraction = require('./lib/os-abstraction.cjs');
const { applyCursorBrowserPolicy } = require('./cursor_browser_policy');

/**
 * ERGO MS User Config Extension
 * 
 * This is a UI Extension that runs on the LOCAL machine.
 * It reads config files from workspace (local or remote) via VS Code API,
 * and writes settings/keybindings to local user config files via filesystem.
 * 
 * Applies project-specific settings and keybindings from:
 * - .vscode/user_settings.json -> global user settings.json
 * - .vscode/user_keybindings.json -> global user keybindings.json
 * 
 * Added settings are marked with "// ERGO MS SETTING" comments.
 * Added keybindings are marked with "// ERGO MS" comments.
 */

let statusBarItem;

/**
 * Check if running in Remote mode (WSL, SSH, Container)
 */
function isRemoteSession() {
    return vscode.env.remoteName !== undefined && vscode.env.remoteName !== null;
}

/**
 * Get remote session type
 */
function getRemoteType() {
    return vscode.env.remoteName || 'local';
}

/**
 * Check if extension is running on the remote host (not local UI)
 * This happens when extensionKind includes "workspace" and extension is only installed on remote
 */
function isRunningOnRemoteHost() {
    // If we're in remote session and process.platform matches remote host (Linux),
    // and the global keybindings path doesn't exist or points to remote path
    if (!isRemoteSession()) {
        return false;
    }
    
    // Check if we can access local keybindings.json
    // If running on remote, this path would be on remote filesystem
    const keybindingsPath = getGlobalKeybindingsPath();
    const keybindingsDir = path.dirname(keybindingsPath);
    
    if (!osAbstraction.isWindows() && isRemoteSession()) {
        return true;
    }
    
    return false;
}

/**
 * Get the path to user config directory (always local since this is UI extension)
 */
function getGlobalConfigDir() {
    const appName = vscode.env.appName.toLowerCase();
    const isCursor = appName.includes('cursor');
    return osAbstraction.getGlobalConfigDir(os.homedir(), appName, isCursor);
}

/**
 * Get the path to user keybindings.json (always local since this is UI extension)
 */
function getGlobalKeybindingsPath() {
    return path.join(getGlobalConfigDir(), 'keybindings.json');
}

/**
 * Get the path to user settings.json (always local since this is UI extension)
 */
function getGlobalSettingsPath() {
    return path.join(getGlobalConfigDir(), 'settings.json');
}

/**
 * Read JSON file with comments support (JSONC) - for local files
 */
function readJsonFileLocal(filePath) {
    try {
        if (!fs.existsSync(filePath)) {
            return null;
        }
        
        let content = fs.readFileSync(filePath, 'utf8');
        
        // Remove single-line comments
        content = content.replace(/\/\/.*$/gm, '');
        // Remove multi-line comments
        content = content.replace(/\/\*[\s\S]*?\*\//g, '');
        // Remove trailing commas
        content = content.replace(/,(\s*[}\]])/g, '$1');
        
        return JSON.parse(content);
    } catch (error) {
        console.error(`Error reading ${filePath}:`, error.message);
        return null;
    }
}

/**
 * Read JSON file from workspace (supports remote workspaces) via VS Code API
 */
async function readJsonFileWorkspace(uri) {
    try {
        const fileContent = await vscode.workspace.fs.readFile(uri);
        let content = Buffer.from(fileContent).toString('utf8');
        
        // Remove single-line comments
        content = content.replace(/\/\/.*$/gm, '');
        // Remove multi-line comments
        content = content.replace(/\/\*[\s\S]*?\*\//g, '');
        // Remove trailing commas
        content = content.replace(/,(\s*[}\]])/g, '$1');
        
        return JSON.parse(content);
    } catch (error) {
        // File doesn't exist or other error
        console.log(`File not found or error reading: ${uri.fsPath}`, error.message);
        return null;
    }
}

/**
 * Write JSON file with pretty formatting
 */
function writeJsonFile(filePath, data) {
    try {
        const dir = path.dirname(filePath);
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
        fs.writeFileSync(filePath, JSON.stringify(data, null, 4), 'utf8');
        return true;
    } catch (error) {
        console.error(`Error writing ${filePath}:`, error.message);
        return false;
    }
}

/**
 * ERGO MS marker for keybindings and settings
 */
const ERGO_MARKER = '// ERGO MS';
const ERGO_SETTINGS_MARKER = '// ERGO MS SETTING';

/**
 * Read settings file and identify ERGO MS settings
 */
function readSettingsFile(filePath) {
    try {
        if (!fs.existsSync(filePath)) {
            return { content: '', settings: {}, ergoKeys: [] };
        }
        
        const content = fs.readFileSync(filePath, 'utf8');
        
        // Parse JSON (removing comments for parsing)
        let cleanContent = content;
        cleanContent = cleanContent.replace(/\/\/.*$/gm, '');
        cleanContent = cleanContent.replace(/\/\*[\s\S]*?\*\//g, '');
        cleanContent = cleanContent.replace(/,(\s*[}\]])/g, '$1');
        
        let settings = {};
        try {
            settings = JSON.parse(cleanContent);
            if (typeof settings !== 'object' || Array.isArray(settings)) {
                settings = {};
            }
        } catch (e) {
            settings = {};
        }
        
        // Find ERGO MS marked settings keys
        const ergoKeys = [];
        const lines = content.split('\n');
        
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            if (line.includes(ERGO_SETTINGS_MARKER)) {
                // Next line should contain the setting key
                const nextLine = lines[i + 1];
                if (nextLine) {
                    const match = nextLine.match(/"([^"]+)":/);
                    if (match) {
                        ergoKeys.push(match[1]);
                    }
                }
            }
        }
        
        return { content, settings, ergoKeys };
    } catch (error) {
        console.error(`Error reading settings ${filePath}:`, error.message);
        return { content: '', settings: {}, ergoKeys: [] };
    }
}

/**
 * Write settings file with ERGO MS markers
 */
function writeSettingsFile(filePath, settings, ergoKeys) {
    try {
        const dir = path.dirname(filePath);
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
        
        // Build JSONC content with markers
        const lines = ['{'];
        const keys = Object.keys(settings);
        
        for (let i = 0; i < keys.length; i++) {
            const key = keys[i];
            const value = settings[key];
            const isErgo = ergoKeys.includes(key);
            const isLast = i === keys.length - 1;
            
            if (isErgo) {
                lines.push(`    ${ERGO_SETTINGS_MARKER}`);
            }
            
            const valueStr = JSON.stringify(value, null, 4)
                .split('\n')
                .map((line, idx) => idx === 0 ? line : '    ' + line)
                .join('\n');
            
            lines.push(`    "${key}": ${valueStr}${isLast ? '' : ','}`);
        }
        
        lines.push('}');
        
        fs.writeFileSync(filePath, lines.join('\n'), 'utf8');
        return true;
    } catch (error) {
        console.error(`Error writing settings ${filePath}:`, error.message);
        return false;
    }
}

/**
 * Read keybindings file preserving structure and comments info
 */
function readKeybindingsFile(filePath) {
    try {
        if (!fs.existsSync(filePath)) {
            return { content: '', keybindings: [], ergoIndices: [] };
        }
        
        const content = fs.readFileSync(filePath, 'utf8');
        
        // Parse JSON (removing comments for parsing)
        let cleanContent = content;
        cleanContent = cleanContent.replace(/\/\/.*$/gm, '');
        cleanContent = cleanContent.replace(/\/\*[\s\S]*?\*\//g, '');
        cleanContent = cleanContent.replace(/,(\s*[}\]])/g, '$1');
        
        let keybindings = [];
        try {
            keybindings = JSON.parse(cleanContent);
            if (!Array.isArray(keybindings)) {
                keybindings = [];
            }
        } catch (e) {
            keybindings = [];
        }
        
        // Find indices of ERGO MS marked keybindings
        const ergoIndices = [];
        const lines = content.split('\n');
        let inErgoBlock = false;
        let braceCount = 0;
        let currentIndex = -1;
        
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            
            if (line.includes(ERGO_MARKER)) {
                inErgoBlock = true;
            }
            
            if (inErgoBlock) {
                if (line.includes('{')) {
                    if (braceCount === 0) {
                        currentIndex++;
                    }
                    braceCount++;
                }
                if (line.includes('}')) {
                    braceCount--;
                    if (braceCount === 0) {
                        ergoIndices.push(currentIndex);
                        inErgoBlock = false;
                    }
                }
            } else {
                if (line.includes('{') && !line.includes('[')) {
                    currentIndex++;
                }
            }
        }
        
        return { content, keybindings, ergoIndices };
    } catch (error) {
        console.error(`Error reading keybindings ${filePath}:`, error.message);
        return { content: '', keybindings: [], ergoIndices: [] };
    }
}

/**
 * Write keybindings file with ERGO MS markers
 */
function writeKeybindingsFile(filePath, keybindings, ergoIndices) {
    try {
        const dir = path.dirname(filePath);
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
        
        // Build JSONC content with markers
        let lines = ['['];
        
        for (let i = 0; i < keybindings.length; i++) {
            const kb = keybindings[i];
            const isErgo = ergoIndices.includes(i);
            const isLast = i === keybindings.length - 1;
            
            if (isErgo) {
                lines.push(`    ${ERGO_MARKER}`);
            }
            
            // Format keybinding object
            const parts = [];
            if (kb.key) parts.push(`"key": ${JSON.stringify(kb.key)}`);
            if (kb.command) parts.push(`"command": ${JSON.stringify(kb.command)}`);
            if (kb.when) parts.push(`"when": ${JSON.stringify(kb.when)}`);
            if (kb.args) parts.push(`"args": ${JSON.stringify(kb.args)}`);
            
            const kbStr = `    { ${parts.join(', ')} }${isLast ? '' : ','}`;
            lines.push(kbStr);
        }
        
        lines.push(']');
        
        fs.writeFileSync(filePath, lines.join('\n'), 'utf8');
        return true;
    } catch (error) {
        console.error(`Error writing keybindings ${filePath}:`, error.message);
        return false;
    }
}

/**
 * Apply settings from user_settings.json to global user settings.json
 */
async function applyUserSettings(workspaceFolderUri, showWarning = false) {
    const settingsUri = vscode.Uri.joinPath(workspaceFolderUri, '.vscode', 'user_settings.json');
    const userSettings = await readJsonFileWorkspace(settingsUri);
    
    if (!userSettings) {
        return { applied: 0, skipped: 0 };
    }
    
    // Check if running on remote host (not local UI)
    if (isRunningOnRemoteHost()) {
        console.log(`Running on remote host (${getRemoteType()}). Settings cannot be applied to local machine.`);
        
        if (showWarning) {
            const action = await vscode.window.showWarningMessage(
                `Расширение запущено на удалённом сервере (${getRemoteType()}). ` +
                `Для автоматического применения settings установите расширение на локальную машину.`,
                'Установить локально',
                'Показать settings'
            );
            
            if (action === 'Установить локально') {
                await vscode.commands.executeCommand('ergo-ms-user-config.installLocal');
            } else if (action === 'Показать settings') {
                const doc = await vscode.workspace.openTextDocument(settingsUri);
                await vscode.window.showTextDocument(doc);
            }
        }
        
        return { applied: 0, skipped: Object.keys(userSettings).length, runningOnRemote: true };
    }
    
    const globalSettingsPath = getGlobalSettingsPath();
    const { settings: globalSettings, ergoKeys } = readSettingsFile(globalSettingsPath);
    
    let applied = 0;
    let skipped = 0;
    const newErgoKeys = [...ergoKeys];
    
    for (const [key, value] of Object.entries(userSettings)) {
        // Check if setting already exists with same value
        if (globalSettings.hasOwnProperty(key) && 
            JSON.stringify(globalSettings[key]) === JSON.stringify(value)) {
            skipped++;
            console.log(`Setting already exists: ${key}`);
        } else {
            // Add or update setting
            globalSettings[key] = value;
            if (!newErgoKeys.includes(key)) {
                newErgoKeys.push(key);
            }
            applied++;
            console.log(`Applied setting: ${key}`);
        }
    }

    const desiredKeys = new Set(Object.keys(userSettings));
    for (const key of [...newErgoKeys]) {
        if (desiredKeys.has(key)) {
            continue;
        }
        delete globalSettings[key];
        const idx = newErgoKeys.indexOf(key);
        if (idx >= 0) {
            newErgoKeys.splice(idx, 1);
        }
        applied++;
        console.log(`Removed stale setting: ${key}`);
    }
    
    if (applied > 0) {
        if (writeSettingsFile(globalSettingsPath, globalSettings, newErgoKeys)) {
            console.log(`Saved ${applied} new settings to ${globalSettingsPath}`);
        } else {
            return { applied: 0, skipped, error: 'Failed to save settings' };
        }
    }
    
    return { applied, skipped };
}

/**
 * Check if keybinding already exists
 */
function keybindingExists(keybindings, newBinding) {
    return keybindings.some(kb => 
        kb.key === newBinding.key && 
        kb.command === newBinding.command &&
        (kb.when || '') === (newBinding.when || '')
    );
}

/**
 * Apply keybindings from user_keybindings.json to global keybindings
 */
async function applyUserKeybindings(workspaceFolderUri, showWarning = false) {
    const projectKeybindingsUri = vscode.Uri.joinPath(workspaceFolderUri, '.vscode', 'user_keybindings.json');
    const projectKeybindings = await readJsonFileWorkspace(projectKeybindingsUri);
    
    if (!projectKeybindings || !Array.isArray(projectKeybindings)) {
        return { applied: 0, skipped: 0 };
    }
    
    // Check if running on remote host (not local UI)
    if (isRunningOnRemoteHost()) {
        console.log(`Running on remote host (${getRemoteType()}). Keybindings cannot be applied to local machine.`);
        
        if (showWarning) {
            const action = await vscode.window.showWarningMessage(
                `Расширение запущено на удалённом сервере (${getRemoteType()}). ` +
                `Для автоматического применения keybindings установите расширение на локальную машину.`,
                'Установить локально',
                'Показать keybindings'
            );
            
            if (action === 'Установить локально') {
                await vscode.commands.executeCommand('ergo-ms-user-config.installLocal');
            } else if (action === 'Показать keybindings') {
                const doc = await vscode.workspace.openTextDocument(projectKeybindingsUri);
                await vscode.window.showTextDocument(doc);
            }
        }
        
        return { applied: 0, skipped: projectKeybindings.length, runningOnRemote: true };
    }
    
    const globalKeybindingsPath = getGlobalKeybindingsPath();
    const { keybindings: globalKeybindings, ergoIndices } = readKeybindingsFile(globalKeybindingsPath);
    
    let applied = 0;
    let skipped = 0;
    const newErgoIndices = [...ergoIndices];
    
    for (const binding of projectKeybindings) {
        if (!binding.key || !binding.command) {
            continue;
        }
        
        if (keybindingExists(globalKeybindings, binding)) {
            skipped++;
            console.log(`Keybinding already exists: ${binding.key} -> ${binding.command}`);
        } else {
            // Add new keybinding and mark its index as ERGO
            const newIndex = globalKeybindings.length;
            globalKeybindings.push(binding);
            newErgoIndices.push(newIndex);
            applied++;
            console.log(`Added keybinding: ${binding.key} -> ${binding.command}`);
        }
    }
    
    if (applied > 0) {
        if (writeKeybindingsFile(globalKeybindingsPath, globalKeybindings, newErgoIndices)) {
            console.log(`Saved ${applied} new keybindings to ${globalKeybindingsPath}`);
        } else {
            return { applied: 0, skipped, error: 'Failed to save keybindings' };
        }
    }
    
    return { applied, skipped };
}

/**
 * Remove all ERGO MS keybindings from global keybindings
 * This runs on the local machine (UI extension), so it has access to local keybindings.json
 */
async function removeErgoKeybindings() {
    const globalKeybindingsPath = getGlobalKeybindingsPath();
    const { keybindings, ergoIndices } = readKeybindingsFile(globalKeybindingsPath);
    
    if (ergoIndices.length === 0) {
        return { removed: 0 };
    }
    
    // Filter out ERGO keybindings
    const filteredKeybindings = keybindings.filter((_, index) => !ergoIndices.includes(index));
    
    if (writeKeybindingsFile(globalKeybindingsPath, filteredKeybindings, [])) {
        console.log(`Removed ${ergoIndices.length} ERGO MS keybindings`);
        return { removed: ergoIndices.length };
    }
    
    return { removed: 0, error: 'Failed to save keybindings' };
}

/**
 * Remove all ERGO MS settings from global settings
 * This runs on the local machine (UI extension), so it has access to local settings.json
 */
async function removeErgoSettings() {
    const globalSettingsPath = getGlobalSettingsPath();
    const { settings, ergoKeys } = readSettingsFile(globalSettingsPath);
    
    if (ergoKeys.length === 0) {
        return { removed: 0 };
    }
    
    // Filter out ERGO settings
    const filteredSettings = {};
    for (const [key, value] of Object.entries(settings)) {
        if (!ergoKeys.includes(key)) {
            filteredSettings[key] = value;
        }
    }
    
    if (writeSettingsFile(globalSettingsPath, filteredSettings, [])) {
        console.log(`Removed ${ergoKeys.length} ERGO MS settings`);
        return { removed: ergoKeys.length };
    }
    
    return { removed: 0, error: 'Failed to save settings' };
}

/**
 * Update status bar
 */
function updateStatusBar(message, timeout = 3000) {
    if (statusBarItem) {
        statusBarItem.text = `$(settings-gear) ${message}`;
        statusBarItem.show();
        
        if (timeout > 0) {
            setTimeout(() => {
                statusBarItem.hide();
            }, timeout);
        }
    }
}

/**
 * Apply all user config
 */
async function applyAllConfig(showNotification = true) {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders || workspaceFolders.length === 0) {
        if (showNotification) {
            vscode.window.showWarningMessage('No workspace folder open');
        }
        return;
    }
    
    const workspaceFolderUri = workspaceFolders[0].uri;
    
    // Apply settings (may not work if running on remote host)
    const settingsResult = await applyUserSettings(workspaceFolderUri, showNotification);
    
    // Apply keybindings (may not work if running on remote host)
    const keybindingsResult = await applyUserKeybindings(workspaceFolderUri, showNotification);
    const browserPolicy = applyCursorBrowserPolicy(workspaceFolderUri);
    
    const totalApplied = settingsResult.applied + keybindingsResult.applied;
    const totalSkipped = settingsResult.skipped + keybindingsResult.skipped;
    const isRunningOnRemote = settingsResult.runningOnRemote || keybindingsResult.runningOnRemote;
    
    if (browserPolicy.error) {
        console.log(`Cursor browser policy error: ${browserPolicy.error}`);
    }

    if (showNotification && totalApplied > 0) {
        vscode.window.showInformationMessage(
            `ERGO MS: Applied ${totalApplied} setting(s)/keybinding(s)`
        );
        updateStatusBar(`Applied ${totalApplied} config(s)`);
    } else if (totalApplied === 0 && totalSkipped > 0 && !isRunningOnRemote) {
        updateStatusBar('Config up to date');
    } else if (isRunningOnRemote) {
        updateStatusBar(`Remote: ${getRemoteType()}`);
    }

    if (browserPolicy.changed) {
        const action = await vscode.window.showInformationMessage(
            'ERGO MS: Cursor больше не будет сам открывать страницы во встроенном браузере. Перезагрузите окно, чтобы политика применилась сейчас.',
            'Перезагрузить'
        );
        if (action === 'Перезагрузить') {
            await vscode.commands.executeCommand('workbench.action.reloadWindow');
        }
    }
    
    return { settingsResult, keybindingsResult };
}

/**
 * Get local extensions directory path
 */
function getLocalExtensionsDir() {
    const appName = vscode.env.appName.toLowerCase();
    const isCursor = appName.includes('cursor');
    return osAbstraction.getLocalExtensionsDir(os.homedir(), isCursor);
}

/**
 * Install extension to local machine (for Remote mode)
 */
async function installExtensionLocally(context) {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (!workspaceFolders) {
        vscode.window.showWarningMessage('No workspace folder open');
        return { success: false };
    }
    
    const workspaceFolderUri = workspaceFolders[0].uri;
    
    try {
        // Read extension files from workspace
        const packageJsonUri = vscode.Uri.joinPath(workspaceFolderUri, '.vscode', 'extensions', 'user-config', 'package.json');
        const extensionJsUri = vscode.Uri.joinPath(workspaceFolderUri, '.vscode', 'extensions', 'user-config', 'extension.js');
        const browserPolicyUri = vscode.Uri.joinPath(workspaceFolderUri, '.vscode', 'extensions', 'user-config', 'cursor_browser_policy.js');
        const iconUri = vscode.Uri.joinPath(workspaceFolderUri, '.vscode', 'extensions', 'user-config', 'icon.png');
        
        const packageJsonContent = await vscode.workspace.fs.readFile(packageJsonUri);
        const extensionJsContent = await vscode.workspace.fs.readFile(extensionJsUri);
        const browserPolicyContent = await vscode.workspace.fs.readFile(browserPolicyUri);
        let iconContent = null;
        try {
            iconContent = await vscode.workspace.fs.readFile(iconUri);
        } catch (_) {
            // icon optional for older checkouts
        }
        
        // Get local extensions directory
        const localExtDir = getLocalExtensionsDir();
        const targetDir = path.join(localExtDir, 'ergo-ms-user-config');
        
        // Create directory
        if (!fs.existsSync(targetDir)) {
            fs.mkdirSync(targetDir, { recursive: true });
        }
        
        // Write files
        fs.writeFileSync(path.join(targetDir, 'package.json'), packageJsonContent);
        fs.writeFileSync(path.join(targetDir, 'extension.js'), extensionJsContent);
        fs.writeFileSync(path.join(targetDir, 'cursor_browser_policy.js'), browserPolicyContent);
        if (iconContent) {
            fs.writeFileSync(path.join(targetDir, 'icon.png'), iconContent);
        }
        
        console.log(`Extension installed to: ${targetDir}`);
        return { success: true, path: targetDir };
    } catch (error) {
        console.error('Failed to install extension locally:', error);
        return { success: false, error: error.message };
    }
}

/**
 * @param {vscode.ExtensionContext} context
 */
function activate(context) {
    console.log('ERGO MS User Config extension is now active');
    console.log(`Running on: ${process.platform}, Remote: ${isRemoteSession() ? getRemoteType() : 'no'}`);
    
    // Create status bar item
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = 'ergo-ms-user-config.applyAll';
    statusBarItem.tooltip = 'Click to apply ERGO MS user config';
    context.subscriptions.push(statusBarItem);
    
    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('ergo-ms-user-config.applySettings', async () => {
            const workspaceFolders = vscode.workspace.workspaceFolders;
            if (!workspaceFolders) {
                vscode.window.showWarningMessage('No workspace folder open');
                return;
            }
            
            const result = await applyUserSettings(workspaceFolders[0].uri, true);
            if (result.error) {
                vscode.window.showErrorMessage(`Settings error: ${result.error}`);
            } else if (!result.runningOnRemote) {
                vscode.window.showInformationMessage(
                    `Settings: ${result.applied} applied, ${result.skipped} already set`
                );
            }
        })
    );
    
    context.subscriptions.push(
        vscode.commands.registerCommand('ergo-ms-user-config.applyKeybindings', async () => {
            const workspaceFolders = vscode.workspace.workspaceFolders;
            if (!workspaceFolders) {
                vscode.window.showWarningMessage('No workspace folder open');
                return;
            }
            
            const result = await applyUserKeybindings(workspaceFolders[0].uri, true);
            if (result.error) {
                vscode.window.showErrorMessage(`Keybindings error: ${result.error}`);
            } else if (!result.runningOnRemote) {
                vscode.window.showInformationMessage(
                    `Keybindings: ${result.applied} applied, ${result.skipped} already exist`
                );
            }
        })
    );
    
    context.subscriptions.push(
        vscode.commands.registerCommand('ergo-ms-user-config.applyAll', () => applyAllConfig(true))
    );
    
    context.subscriptions.push(
        vscode.commands.registerCommand('ergo-ms-user-config.removeKeybindings', async () => {
            const result = await removeErgoKeybindings();
            if (result.error) {
                vscode.window.showErrorMessage(`Remove keybindings error: ${result.error}`);
            } else if (result.removed > 0) {
                vscode.window.showInformationMessage(
                    `Removed ${result.removed} ERGO MS keybinding(s)`
                );
            } else {
                vscode.window.showInformationMessage('No ERGO MS keybindings found');
            }
        })
    );
    
    context.subscriptions.push(
        vscode.commands.registerCommand('ergo-ms-user-config.removeSettings', async () => {
            const result = await removeErgoSettings();
            if (result.error) {
                vscode.window.showErrorMessage(`Remove settings error: ${result.error}`);
            } else if (result.removed > 0) {
                vscode.window.showInformationMessage(
                    `Removed ${result.removed} ERGO MS setting(s)`
                );
            } else {
                vscode.window.showInformationMessage('No ERGO MS settings found');
            }
        })
    );
    
    // Command to install extension locally (useful for Remote mode)
    context.subscriptions.push(
        vscode.commands.registerCommand('ergo-ms-user-config.installLocal', async () => {
            const result = await installExtensionLocally(context);
            if (result.success) {
                const action = await vscode.window.showInformationMessage(
                    `Расширение установлено в: ${result.path}. Перезагрузите Cursor для активации.`,
                    'Перезагрузить'
                );
                if (action === 'Перезагрузить') {
                    await vscode.commands.executeCommand('workbench.action.reloadWindow');
                }
            } else {
                vscode.window.showErrorMessage(
                    `Ошибка установки расширения: ${result.error || 'Unknown error'}`
                );
            }
        })
    );
    
    // Watch for changes in config files
    const settingsWatcher = vscode.workspace.createFileSystemWatcher('**/.vscode/user_settings.json');
    const keybindingsWatcher = vscode.workspace.createFileSystemWatcher('**/.vscode/user_keybindings.json');
    
    settingsWatcher.onDidChange(() => {
        console.log('user_settings.json changed, reapplying...');
        applyAllConfig(false);
    });
    
    keybindingsWatcher.onDidChange(() => {
        console.log('user_keybindings.json changed, reapplying...');
        applyAllConfig(false);
    });
    
    context.subscriptions.push(settingsWatcher);
    context.subscriptions.push(keybindingsWatcher);
    
    // Apply config on activation
    applyAllConfig(false);
}

function deactivate() {
    if (statusBarItem) {
        statusBarItem.dispose();
    }
}

module.exports = {
    activate,
    deactivate
};

