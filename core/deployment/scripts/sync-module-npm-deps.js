/**
 * Сканирует package.json в modules/<name>/client и проверяет, что все модульные
 * npm-зависимости установлены в virtual_env/npm/node_modules (hoisted).
 *
 * Установка — через npm install <pkg>@<ver> --no-save --no-package-lock,
 * чтобы модульные пакеты не попадали в package-lock.json npm-root.
 *
 * С --install-missing / --update / --install-all: снимает только пакеты вне
 * дерева ядра и включённых модулей (не `npm prune` — он считает --no-save
 * лишними и каждый раз вынуждает ставить модули заново). Обход node_modules
 * для очистки пропускается, если набор прямых пакетов не менялся. Затем
 * доустанавливает недостающие модульные пакеты. Кэш npm чистится, только
 * если что-то сняли.
 *
 * С --install-all — ставит ядро целиком только если node_modules пуст или нет
 * ссылок workspace. Недостающие пакеты ядра ставятся отдельно в staging, без
 * повторного разрешения уже стоящего Vue. Сменившийся lock при пакетах на
 * месте только обновляет отпечаток. Пакеты модулей тоже доустанавливаются
 * в staging и копируются, чтобы `npm install` в npm-root не снимал --no-save
 * пакеты и не упирался в peer-зависимости.
 * С --update — переустанавливает модульные пакеты в пределах semver из package.json.
 * С --check — код 0, если прямые пакеты ядра и модулей есть в node_modules, иначе 1.
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDisabledModules } from '../../../core/client/scripts/lib/parse-disabled-modules.js'
import { runNpm } from './run_npm_spawn.js'
import {
  collectCoreDirectNames,
  collectKeepDirectNames,
  collectMissingCoreInstallSpecs,
  isCoreTreeCurrent,
  isKeepTreeCurrent,
  moduleSpecsObject,
  nodeModulesIsEmpty,
  pruneUnreachableTopLevelPackages,
  readModuleSpecsStamp,
  workspaceLinksMissing,
  writeCoreTreeStamp,
  writeKeepTreeStamp,
  writeModuleSpecsStamp,
} from './sync-module-npm-tree.js'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const NPM_ROOT = path.join(ROOT, 'virtual_env', 'npm')
const NPM_CACHE = path.join(ROOT, 'virtual_env', 'cache', 'npm')
const CACHE_TMP = path.join(ROOT, 'virtual_env', 'cache', 'tmp')
const MODULES_ROOT = path.join(ROOT, 'modules')
const NODE_MODULES = path.join(NPM_ROOT, 'node_modules')
const INSTALL_ALL = process.argv.includes('--install-all')
const UPDATE = process.argv.includes('--update')
const CHECK_ONLY = process.argv.includes('--check')
const INSTALL_MISSING = process.argv.includes('--install-missing') || INSTALL_ALL
const INSTALL_FLAGS = [
  '--no-save',
  '--ignore-scripts',
  '--no-package-lock',
  '--no-audit',
  '--no-fund',
  '--legacy-peer-deps',
  '--loglevel=http',
  '--fetch-retries=5',
  '--fetch-retry-mintimeout=2000',
  '--fetch-retry-maxtimeout=15000',
]
const PACKAGE_FILTERS = process.argv
  .slice(2)
  .filter((arg) => arg && !arg.startsWith('-'))
const TMP_PREFIXES = ['ergo_install_', 'ergo_update_', 'ergo-npm-', 'ergo_npm_', 'ergo-npm-mod-']

function collectModuleDependencies() {
  if (!fs.existsSync(MODULES_ROOT)) {
    return []
  }

  const disabledModules = loadDisabledModules()
  const entries = []

  for (const dirent of fs.readdirSync(MODULES_ROOT, { withFileTypes: true })) {
    if (!dirent.isDirectory()) {
      continue
    }
    if (disabledModules.has(dirent.name)) {
      continue
    }

    const packageJsonPath = path.join(MODULES_ROOT, dirent.name, 'client', 'package.json')
    if (!fs.existsSync(packageJsonPath)) {
      continue
    }

    const pkg = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'))
    const dependencies = pkg.dependencies ?? {}

    for (const [depName, depVersion] of Object.entries(dependencies)) {
      entries.push({
        module: dirent.name,
        depName,
        depVersion,
      })
    }
  }

  return entries
}

function isDependencyInstalled(depName) {
  // Vite резолвит только hoisted node_modules — вложенные копии в modules/*/client не считаем.
  return fs.existsSync(path.join(NPM_ROOT, 'node_modules', depName))
}

function hasMissingDirectPackages(moduleDeps) {
  for (const name of collectCoreDirectNames(NPM_ROOT)) {
    if (!isDependencyInstalled(name)) {
      return true
    }
  }
  return moduleDeps.some((entry) => !isDependencyInstalled(entry.depName))
}

function uniqueMissingPackages(missing) {
  const byName = new Map()
  for (const entry of missing) {
    if (!byName.has(entry.depName)) {
      byName.set(entry.depName, entry)
    }
  }
  return [...byName.values()]
}

function modulePackageSpecs(moduleDeps) {
  return uniqueMissingPackages(moduleDeps).map(
    (entry) => `${entry.depName}@${entry.depVersion}`,
  )
}


function latestNpmDebugLogText() {
  const logsDir = path.join(NPM_CACHE, '_logs')
  if (!fs.existsSync(logsDir)) {
    return ''
  }
  let newestPath = ''
  let newestMtime = 0
  for (const entry of fs.readdirSync(logsDir, { withFileTypes: true })) {
    if (!entry.isFile()) {
      continue
    }
    const full = path.join(logsDir, entry.name)
    try {
      const mtime = fs.statSync(full).mtimeMs
      if (mtime >= newestMtime) {
        newestMtime = mtime
        newestPath = full
      }
    } catch {
      // ignore
    }
  }
  if (!newestPath) {
    return ''
  }
  try {
    return fs.readFileSync(newestPath, 'utf8')
  } catch {
    return ''
  }
}

function npmFailureLooksLikeCorruptCache() {
  const lower = latestNpmDebugLogText().toLowerCase()
  if (!lower.includes('enoent')) {
    return false
  }
  return (
    lower.includes('_cacache')
    || lower.includes('content-v2')
    || lower.includes('invalid response body')
  )
}

function repairNpmCache({ purge = false } = {}) {
  if (purge) {
    console.log('[npm] Кэш npm повреждён — полная очистка (_cacache).')
    runNpm(['cache', 'clean', '--force'], { cwd: NPM_ROOT, stdio: 'inherit' })
    return
  }
  console.log('[npm] Кэш npm повреждён — проверка и сборка (_cacache verify).')
  runNpm(['cache', 'verify'], { cwd: NPM_ROOT, stdio: 'inherit' })
}

function runNpmInstallWithCacheRepair(runOnce) {
  let result = runOnce()
  if (!result || result.status === 0) {
    return result
  }
  if (!npmFailureLooksLikeCorruptCache()) {
    return result
  }
  repairNpmCache({ purge: false })
  result = runOnce()
  if (!result || result.status === 0) {
    return result
  }
  if (!npmFailureLooksLikeCorruptCache()) {
    return result
  }
  repairNpmCache({ purge: true })
  return runOnce()
}

function copyPackageTree(sourceDir, targetDir) {
  if (fs.existsSync(targetDir)) {
    fs.rmSync(targetDir, { recursive: true, force: true })
  }
  fs.cpSync(sourceDir, targetDir, { recursive: true, force: true })
}

function installPackageSpecsInStaging(specs, label) {
  const cacheTmp = path.join(ROOT, 'virtual_env', 'cache', 'tmp')
  fs.mkdirSync(cacheTmp, { recursive: true })
  const staging = fs.mkdtempSync(path.join(cacheTmp, 'ergo-npm-mod-'))

  console.log(`[npm] ${label} в staging (${specs.length}): ${specs.join(', ')}`)
  console.log('[npm] Запросы в registry видны ниже; таймаут 60 с, до 5 повторов.')
  fs.writeFileSync(
    path.join(staging, 'package.json'),
    `${JSON.stringify({ name: 'ergo-npm-staging', private: true, version: '0.0.0' })}\n`,
    'utf8',
  )
  fs.writeFileSync(
    path.join(staging, '.npmrc'),
    [
      'install-strategy=hoisted',
      'package-lock=false',
      'legacy-peer-deps=true',
      'fetch-timeout=60000',
      'fetch-retries=5',
      'fetch-retry-mintimeout=2000',
      'fetch-retry-maxtimeout=15000',
      'progress=true',
      '',
    ].join('\n'),
    'utf8',
  )

  try {
    const result = runNpmInstallWithCacheRepair(() => runNpm(
      ['install', ...specs, ...INSTALL_FLAGS, '--prefix', staging],
      { cwd: staging },
    ))

    if (!result || result.status !== 0) {
      process.exit(result?.status ?? 1)
    }

    const stagingModules = path.join(staging, 'node_modules')
    if (!fs.existsSync(stagingModules)) {
      console.error(`[npm] Staging node_modules не появился: ${stagingModules}`)
      process.exit(1)
    }
    const targetModules = path.join(NPM_ROOT, 'node_modules')
    fs.mkdirSync(targetModules, { recursive: true })

    for (const entry of fs.readdirSync(stagingModules)) {
      if (entry.startsWith('.')) {
        continue
      }
      copyPackageTree(
        path.join(stagingModules, entry),
        path.join(targetModules, entry),
      )
    }
  } finally {
    fs.rmSync(staging, { recursive: true, force: true })
  }
}

function filterModuleDeps(moduleDeps) {
  if (PACKAGE_FILTERS.length === 0) {
    return moduleDeps
  }
  const wanted = new Set(PACKAGE_FILTERS.map((name) => name.toLowerCase()))
  return moduleDeps.filter((entry) => wanted.has(entry.depName.toLowerCase()))
}

function installPackageSpecs(specs, label) {
  if (specs.length === 0) {
    return
  }

  installPackageSpecsInStaging(specs, label)
}

function installMissingPackages(missing) {
  installPackageSpecs(modulePackageSpecs(missing), 'Доустановка пакетов')
}

function updateModulePackages(moduleDeps) {
  installPackageSpecs(
    modulePackageSpecs(moduleDeps),
    'Обновление модульных пакетов в пределах semver',
  )
}

function prunePackagesOutsideKeepTree(moduleDeps, { force = false } = {}) {
  const keepDirect = collectKeepDirectNames(
    NPM_ROOT,
    moduleDeps.map((entry) => entry.depName),
  )
  if (!force && isKeepTreeCurrent(NODE_MODULES, keepDirect)) {
    console.log('[npm] Состав прямых пакетов без изменений — очистка node_modules пропущена.')
    return []
  }
  const removed = pruneUnreachableTopLevelPackages(NODE_MODULES, keepDirect)
  writeKeepTreeStamp(NODE_MODULES, keepDirect)
  if (removed.length === 0) {
    console.log('[npm] Лишних пакетов в node_modules нет.')
    return removed
  }

  const preview = removed.length <= 20 ? `: ${removed.join(', ')}` : ''
  console.log(
    `[npm] Удаление пакетов вне дерева ядра и модулей (${removed.length})${preview}`,
  )
  return removed
}

function installCorePackagesIfNeeded() {
  if (isCoreTreeCurrent({
    npmRoot: NPM_ROOT,
    nodeModules: NODE_MODULES,
  })) {
    console.log('[npm] Зависимости ядра уже установлены — npm install пропущен.')
    return false
  }

  const missingSpecs = collectMissingCoreInstallSpecs(NPM_ROOT, NODE_MODULES)
  const needFullInstall = nodeModulesIsEmpty(NODE_MODULES)
    || workspaceLinksMissing(NPM_ROOT, NODE_MODULES)

  if (!needFullInstall && missingSpecs.length === 0) {
    writeCoreTreeStamp(NPM_ROOT, NODE_MODULES)
    console.log('[npm] Пакеты ядра на месте — отпечаток lock обновлён, npm install пропущен.')
    return false
  }

  if (needFullInstall) {
    console.log('[npm] Установка зависимостей ядра (node_modules пуст или нет ссылок workspace)...')
    const result = runNpmInstallWithCacheRepair(
      () => runNpm(['install', ...INSTALL_FLAGS], { cwd: NPM_ROOT }),
    )
    if (!result || result.status !== 0) {
      process.exit(result?.status ?? 1)
    }
  } else {
    installPackageSpecs(missingSpecs, 'Доустановка пакетов ядра')
  }

  writeCoreTreeStamp(NPM_ROOT, NODE_MODULES)
  return true
}

function ensureNpmCacheEnv() {
  fs.mkdirSync(NPM_CACHE, { recursive: true })
  process.env.npm_config_cache = NPM_CACHE
  process.env.NPM_CONFIG_CACHE = NPM_CACHE
  // Staging не читает virtual_env/npm/.npmrc — те же пределы, что в npm-root.
  if (!process.env.npm_config_fetch_timeout) {
    process.env.npm_config_fetch_timeout = '60000'
  }
  if (!process.env.npm_config_fetch_retries) {
    process.env.npm_config_fetch_retries = '2'
  }
}

function cleanCacheTmp() {
  if (!fs.existsSync(CACHE_TMP)) {
    return
  }
  let removed = 0
  for (const entry of fs.readdirSync(CACHE_TMP, { withFileTypes: true })) {
    if (!TMP_PREFIXES.some((prefix) => entry.name.startsWith(prefix))) {
      continue
    }
    const full = path.join(CACHE_TMP, entry.name)
    try {
      fs.rmSync(full, { recursive: true, force: true })
      removed += 1
    } catch {
      // ignore
    }
  }
  if (removed > 0) {
    console.log(`[npm] Очистка virtual_env/cache/tmp: удалено ${removed} временных путей.`)
  }
}

function collectInstalledPackageNames(nodeModulesDir, names = new Set()) {
  if (!fs.existsSync(nodeModulesDir)) {
    return names
  }
  for (const entry of fs.readdirSync(nodeModulesDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith('.')) {
      continue
    }
    if (entry.name.startsWith('@')) {
      const scopeDir = path.join(nodeModulesDir, entry.name)
      for (const pkg of fs.readdirSync(scopeDir, { withFileTypes: true })) {
        if (!pkg.isDirectory()) {
          continue
        }
        const pkgDir = path.join(scopeDir, pkg.name)
        readPackageName(pkgDir, names)
        collectInstalledPackageNames(path.join(pkgDir, 'node_modules'), names)
      }
      continue
    }
    const pkgDir = path.join(nodeModulesDir, entry.name)
    readPackageName(pkgDir, names)
    collectInstalledPackageNames(path.join(pkgDir, 'node_modules'), names)
  }
  return names
}

function readPackageName(pkgDir, names) {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf8'))
    if (pkg.name) {
      names.add(String(pkg.name).toLowerCase())
    }
  } catch {
    // ignore broken/incomplete installs
  }
}

function parsePackageFromNpmCacheKey(key) {
  const marker = 'registry.npmjs.org/'
  const idx = key.indexOf(marker)
  if (idx < 0) {
    return null
  }
  let rest = key.slice(idx + marker.length)
  try {
    rest = decodeURIComponent(rest)
  } catch {
    // keep raw
  }
  if (rest.startsWith('@')) {
    const match = rest.match(/^(@[^/]+\/[^/]+)/)
    return match ? match[1].toLowerCase() : null
  }
  const match = rest.match(/^([^/@]+)/)
  return match ? match[1].toLowerCase() : null
}

function cleanNpmLogs() {
  const logsDir = path.join(NPM_CACHE, '_logs')
  if (!fs.existsSync(logsDir)) {
    return
  }
  let removed = 0
  for (const entry of fs.readdirSync(logsDir, { withFileTypes: true })) {
    if (!entry.isFile()) {
      continue
    }
    try {
      fs.rmSync(path.join(logsDir, entry.name), { force: true })
      removed += 1
    } catch {
      // ignore
    }
  }
  if (removed > 0) {
    console.log(`[npm] Кэш npm: удалено ${removed} файлов логов.`)
  }
}

function pruneUnusedNpmCache() {
  ensureNpmCacheEnv()
  cleanCacheTmp()
  cleanNpmLogs()

  const keep = collectInstalledPackageNames(NODE_MODULES)
  if (keep.size === 0) {
    console.log('[npm] Кэш npm: node_modules пуст — очистка кэша пропущена.')
    return
  }

  const listed = runNpm(['cache', 'ls'], {
    cwd: NPM_ROOT,
    stdio: 'pipe',
    encoding: 'utf8',
  })
  if (listed.status !== 0) {
    console.log('[npm] Кэш npm: не удалось получить список (cache ls).')
    return
  }

  const keys = String(listed.stdout || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

  const toClean = []
  const unusedPackages = new Set()
  for (const key of keys) {
    const pkg = parsePackageFromNpmCacheKey(key)
    if (!pkg || keep.has(pkg)) {
      continue
    }
    toClean.push(key)
    unusedPackages.add(pkg)
  }

  if (toClean.length === 0) {
    console.log('[npm] Кэш npm: лишних индексных записей нет.')
  } else {
    console.log(
      `[npm] Кэш npm: удаление ${toClean.length} записей (${unusedPackages.size} пакетов вне node_modules)...`,
    )
    for (const key of toClean) {
      runNpm(['cache', 'clean', key], {
        cwd: NPM_ROOT,
        stdio: 'pipe',
        encoding: 'utf8',
      })
    }
  }

  // cache clean по ключу рвёт индекс _cacache (лишние blob'ы или запись без файла) — verify собирает.
  console.log('[npm] Кэш npm: проверка и сжатие (_cacache verify)...')
  runNpm(['cache', 'verify'], {
    cwd: NPM_ROOT,
    stdio: 'inherit',
  })
}

function specsNeedingInstall(moduleDeps) {
  const current = moduleSpecsObject(moduleDeps)
  const stamped = readModuleSpecsStamp(NODE_MODULES)
  const needed = []
  for (const [depName, depVersion] of Object.entries(current)) {
    if (!isDependencyInstalled(depName)) {
      needed.push({ depName, depVersion })
      continue
    }
    if (stamped && Object.prototype.hasOwnProperty.call(stamped, depName) && stamped[depName] !== depVersion) {
      needed.push({ depName, depVersion })
    }
  }
  return needed
}

function ensureModulePackagesInstalled(moduleDeps) {
  if (moduleDeps.length === 0) {
    console.log('[npm] Модульных npm-зависимостей не найдено.')
    writeModuleSpecsStamp(NODE_MODULES, moduleDeps)
    return
  }

  const uniqueAll = uniqueMissingPackages(moduleDeps)
  const needed = specsNeedingInstall(uniqueAll)
  if (needed.length === 0) {
    console.log(`[npm] Модульные зависимости (${uniqueAll.length}) установлены.`)
    writeModuleSpecsStamp(NODE_MODULES, uniqueAll)
    return
  }

  console.log(`[npm] Не установлено пакетов: ${needed.length}`)
  for (const entry of needed) {
    console.log(`  - ${entry.depName}`)
  }

  installMissingPackages(needed)

  const stillMissing = needed.filter((entry) => !isDependencyInstalled(entry.depName))
  if (stillMissing.length > 0) {
    console.error('[npm] Не удалось установить пакеты:')
    for (const entry of uniqueMissingPackages(stillMissing)) {
      const sources = moduleDeps
        .filter((item) => item.depName === entry.depName)
        .map((item) => item.module)
      console.error(`  - ${entry.depName} (модули: ${sources.join(', ')})`)
    }
    process.exit(1)
  }

  writeModuleSpecsStamp(NODE_MODULES, uniqueAll)
  console.log('[npm] Модульные зависимости успешно установлены.')
}

function main() {
  const allModuleDeps = collectModuleDependencies()
  const moduleDeps = filterModuleDeps(allModuleDeps)
  if (CHECK_ONLY) {
    process.exit(hasMissingDirectPackages(allModuleDeps) ? 1 : 0)
  }
  ensureNpmCacheEnv()
  const applyChanges = INSTALL_MISSING || UPDATE

  if (!applyChanges) {
    if (moduleDeps.length === 0) {
      console.log('[npm] Модульных npm-зависимостей не найдено.')
      return
    }

    const missing = moduleDeps.filter((entry) => !isDependencyInstalled(entry.depName))
    if (missing.length === 0) {
      console.log(`[npm] Модульные зависимости (${moduleDeps.length}) установлены.`)
      return
    }

    const uniqueMissing = uniqueMissingPackages(missing)
    console.log(`[npm] Не установлено пакетов: ${uniqueMissing.length}`)
    for (const entry of uniqueMissing) {
      console.log(`  - ${entry.depName}`)
    }
    console.log('[npm] Запустите: ergoms npm run install:all')
    return
  }

  let coreInstalled = false
  if (INSTALL_ALL) {
    coreInstalled = installCorePackagesIfNeeded()
  }

  // Свой prune: `npm prune` снял бы --no-save пакеты модулей.
  const removed = prunePackagesOutsideKeepTree(allModuleDeps, {
    force: UPDATE || coreInstalled,
  })

  if (UPDATE) {
    if (moduleDeps.length === 0) {
      console.log('[npm] Модульных npm-зависимостей для обновления не найдено.')
    } else {
      updateModulePackages(moduleDeps)
      console.log('[npm] Модульные зависимости обновлены.')
    }
  }

  ensureModulePackagesInstalled(allModuleDeps)
  if (removed.length > 0) {
    pruneUnusedNpmCache()
  }
}

main()
