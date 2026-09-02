import { readdir, readFile } from 'fs/promises';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
import { existsSync } from 'fs';
import os from 'os';
import {
  runCodeCli,
  uninstallExtensionFromDirs,
} from '../lib/extension-cli.js';

const require = createRequire(import.meta.url);
const osAbstraction = require('../lib/os-abstraction.cjs');

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const projectRoot = join(__dirname, '..');
const localExtensionsDir = join(projectRoot, 'local-extensions');
const tempRoot = join(projectRoot, '.temp-extract');

async function getExtensionIdFromSource(fileName) {
  try {
    const withoutExt = fileName.replace(/\.vsix$/, '');
    const versionMatch = withoutExt.match(/-(\d+\.\d+\.\d+)$/);

    if (!versionMatch) {
      return null;
    }

    const extensionName = withoutExt.substring(0, versionMatch.index);
    const extensionsDir = join(projectRoot, 'extensions');
    const possibleDirs = [
      extensionName,
      extensionName.replace('ergo-ms-', ''),
    ];

    for (const dirName of possibleDirs) {
      const extensionDir = join(extensionsDir, dirName);
      const packageJsonPath = join(extensionDir, 'package.json');

      if (existsSync(packageJsonPath)) {
        const packageJsonContent = await readFile(packageJsonPath, 'utf-8');
        const packageJson = JSON.parse(packageJsonContent);
        const { publisher, name } = packageJson;

        if (publisher && name) {
          return `${publisher}.${name}`;
        }

        if (name) {
          return name;
        }
      }
    }

    if (extensionName.startsWith('ergo-ms-')) {
      return extensionName.replace('ergo-ms-', 'ergo-ms.');
    }

    return extensionName;
  } catch (error) {
    console.error(`Ошибка при определении ID расширения для ${fileName}:`, error.message);
    return null;
  }
}

function uninstallExtensionViaCodeCli(extensionId) {
  try {
    console.log(`🗑️  Удаление расширения через code CLI: ${extensionId}`);
    runCodeCli(`code --uninstall-extension "${extensionId}" --force`, { cwd: projectRoot });
    console.log(`✅ Успешно удалено: ${extensionId}`);
    return true;
  } catch (error) {
    if (error.message.includes('is not installed') || error.status === 1) {
      console.log(`ℹ️  Расширение не установлено: ${extensionId}`);
      return false;
    }
    console.error(`❌ Ошибка при удалении ${extensionId}:`, error.message);
    return false;
  }
}

async function uninstallUserConfigFromRemote() {
  if (!osAbstraction.supportsRemoteInstall()) {
    return false;
  }

  try {
    const fs = await import('fs/promises');
    const homeDir = os.homedir();
    const remoteDirs = [
      join(homeDir, '.vscode-server', 'extensions'),
      join(homeDir, '.cursor-server', 'extensions'),
    ];

    let removed = false;
    const names = new Set(['ergo-ms-user-config-1.2.0']);
    const sourcePkg = join(projectRoot, 'extensions', 'user-config', 'package.json');
    if (existsSync(sourcePkg)) {
      try {
        const pkg = JSON.parse(await readFile(sourcePkg, 'utf-8'));
        if (pkg.publisher && pkg.name && pkg.version) {
          names.add(`${pkg.publisher}.${pkg.name}-${pkg.version}`);
        }
      } catch {
        // оставляем запасное старое имя каталога
      }
    }

    for (const remoteDir of remoteDirs) {
      for (const extensionName of names) {
        const targetDir = join(remoteDir, extensionName);
        if (!existsSync(targetDir)) {
          continue;
        }
        try {
          await fs.rm(targetDir, { recursive: true, force: true });
          console.log(`  [OK] Удалено с удаленного сервера: ${remoteDir}`);
          removed = true;
        } catch {
          console.log(`  [WARNING] Не удалось удалить с удаленного сервера: ${remoteDir}`);
        }
      }
    }

    return removed;
  } catch (error) {
    console.error(`  ⚠️  Ошибка при удалении с удаленного сервера: ${error.message}`);
    return false;
  }
}

async function uninstallExtensions() {
  try {
    if (!existsSync(localExtensionsDir)) {
      console.log('❌ Директория local-extensions не найдена');
      return;
    }

    const files = await readdir(localExtensionsDir);
    const vsixFiles = files.filter(file => file.endsWith('.vsix'));

    if (vsixFiles.length === 0) {
      console.log('❌ VSIX файлы не найдены в папке local-extensions');
      return;
    }

    console.log(`🗑️  Найдено ${vsixFiles.length} расширений для удаления:`);
    const homeDir = os.homedir();

    for (const file of vsixFiles) {
      const filePath = join(localExtensionsDir, file);
      const isUserConfig = file.includes('user-config');

      console.log(`\n🗑️  Удаление: ${file}`);

      let uninstalled = false;
      try {
        uninstalled = await uninstallExtensionFromDirs(filePath, homeDir, tempRoot);
      } catch (error) {
        console.log(`  ⚠️  Не удалось удалить из директорий расширений: ${error.message}`);
      }

      if (uninstalled) {
        console.log(`✅ Успешно удалено из директорий расширений: ${file}`);
      } else {
        const extensionId = await getExtensionIdFromSource(file);
        if (!extensionId) {
          console.log(`⚠️  Не удалось определить ID расширения: ${file}`);
          continue;
        }
        uninstalled = uninstallExtensionViaCodeCli(extensionId);
      }

      if (isUserConfig && uninstalled) {
        console.log(`\n🌐 Удаление с удаленного сервера: ${file}`);
        await uninstallUserConfigFromRemote();
      }
    }

    console.log('\n✨ Удаление расширений завершено!');
  } catch (error) {
    if (error.code === 'ENOENT') {
      console.error('❌ Директория local-extensions не найдена');
    } else {
      console.error('❌ Ошибка:', error.message);
    }
    process.exit(1);
  }
}

uninstallExtensions();
