/**
 * Дерево npm, которое нельзя считать «лишним»: зависимости ядра, workspace
 * и прямые пакеты модулей плюс всё, до чего от них можно дойти в node_modules.
 *
 * `npm prune` этого не умеет — пакеты из `npm install --no-save` он снимает
 * как посторонние, и install:all тогда каждый раз ставит их заново.
 *
 * Актуальность ядра — по наличию прямых пакетов и отпечатку package-lock.json,
 * не по mtime package.json: правка скриптов иначе снова гоняла бы npm install.
 */

import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

const CORE_TREE_STAMP = '.ergo-core-tree-ok'
const KEEP_TREE_STAMP = '.ergo-keep-tree-ok'
const MODULE_SPECS_STAMP = '.ergo-module-specs-ok'

function readPackageJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'))
}

function depNamesFromPackage(pkg) {
  return [
    ...Object.keys(pkg.dependencies ?? {}),
    ...Object.keys(pkg.devDependencies ?? {}),
    ...Object.keys(pkg.optionalDependencies ?? {}),
    ...Object.keys(pkg.peerDependencies ?? {}),
  ]
}

export function collectCoreDirectNames(npmRoot) {
  const names = new Set()
  const rootPkgPath = path.join(npmRoot, 'package.json')
  if (!fs.existsSync(rootPkgPath)) {
    return names
  }

  const rootPkg = readPackageJson(rootPkgPath)
  for (const name of depNamesFromPackage(rootPkg)) {
    names.add(name)
  }

  for (const workspace of rootPkg.workspaces ?? []) {
    if (typeof workspace !== 'string') {
      continue
    }
    const workspacePkgPath = path.resolve(npmRoot, workspace, 'package.json')
    if (!fs.existsSync(workspacePkgPath)) {
      continue
    }
    const workspacePkg = readPackageJson(workspacePkgPath)
    if (workspacePkg.name) {
      names.add(String(workspacePkg.name))
    }
    for (const name of depNamesFromPackage(workspacePkg)) {
      names.add(name)
    }
  }

  return names
}

export function collectKeepDirectNames(npmRoot, extraNames = []) {
  const names = collectCoreDirectNames(npmRoot)
  for (const name of extraNames) {
    if (name) {
      names.add(name)
    }
  }
  return names
}

export function listTopLevelPackageNames(nodeModulesDir) {
  const names = []
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
        if (pkg.isDirectory()) {
          names.push(`${entry.name}/${pkg.name}`)
        }
      }
      continue
    }
    names.push(entry.name)
  }

  return names
}

function packageDir(nodeModulesDir, name) {
  return path.join(nodeModulesDir, name)
}

function readInstalledDepNames(nodeModulesDir, name) {
  const pkgFile = path.join(packageDir(nodeModulesDir, name), 'package.json')
  if (!fs.existsSync(pkgFile)) {
    return []
  }
  try {
    return depNamesFromPackage(readPackageJson(pkgFile))
  } catch {
    return []
  }
}

export function collectReachableNames(nodeModulesDir, keepDirect) {
  const reachable = new Set()
  const queue = [...keepDirect]

  while (queue.length > 0) {
    const name = queue.pop()
    if (!name || reachable.has(name)) {
      continue
    }
    if (!fs.existsSync(packageDir(nodeModulesDir, name))) {
      continue
    }
    reachable.add(name)
    for (const dep of readInstalledDepNames(nodeModulesDir, name)) {
      if (!reachable.has(dep)) {
        queue.push(dep)
      }
    }
  }

  return reachable
}

function removeTopLevelPackage(nodeModulesDir, name) {
  fs.rmSync(packageDir(nodeModulesDir, name), { recursive: true, force: true })
  if (!name.startsWith('@')) {
    return
  }
  const scope = name.slice(0, name.indexOf('/'))
  if (!scope) {
    return
  }
  const scopeDir = path.join(nodeModulesDir, scope)
  if (!fs.existsSync(scopeDir)) {
    return
  }
  const leftover = fs.readdirSync(scopeDir).filter((entry) => !entry.startsWith('.'))
  if (leftover.length === 0) {
    fs.rmSync(scopeDir, { recursive: true, force: true })
  }
}

export function pruneUnreachableTopLevelPackages(nodeModulesDir, keepDirect) {
  if (!fs.existsSync(nodeModulesDir)) {
    return []
  }

  const reachable = collectReachableNames(nodeModulesDir, keepDirect)
  const removed = []

  for (const name of listTopLevelPackageNames(nodeModulesDir)) {
    if (reachable.has(name)) {
      continue
    }
    removeTopLevelPackage(nodeModulesDir, name)
    removed.push(name)
  }

  return removed
}

function lockfileFingerprint(npmRoot) {
  const lockPath = path.join(npmRoot, 'package-lock.json')
  if (!fs.existsSync(lockPath)) {
    return ''
  }
  return crypto.createHash('sha256').update(fs.readFileSync(lockPath)).digest('hex')
}

function coreTreeStampFile(nodeModulesDir) {
  return path.join(nodeModulesDir, CORE_TREE_STAMP)
}

export function writeCoreTreeStamp(npmRoot, nodeModules) {
  fs.mkdirSync(nodeModules, { recursive: true })
  fs.writeFileSync(coreTreeStampFile(nodeModules), `${lockfileFingerprint(npmRoot)}\n`, 'utf8')
}

function keepDirectFingerprint(keepDirect) {
  return crypto.createHash('sha256').update([...keepDirect].sort().join('\n')).digest('hex')
}

export function isKeepTreeCurrent(nodeModules, keepDirect) {
  const stampPath = path.join(nodeModules, KEEP_TREE_STAMP)
  if (!fs.existsSync(stampPath)) {
    return false
  }
  try {
    return fs.readFileSync(stampPath, 'utf8').trim() === keepDirectFingerprint(keepDirect)
  } catch {
    return false
  }
}

export function writeKeepTreeStamp(nodeModules, keepDirect) {
  fs.mkdirSync(nodeModules, { recursive: true })
  fs.writeFileSync(
    path.join(nodeModules, KEEP_TREE_STAMP),
    `${keepDirectFingerprint(keepDirect)}\n`,
    'utf8',
  )
}

export function moduleSpecsObject(moduleDeps) {
  const out = {}
  for (const entry of moduleDeps) {
    if (entry?.depName) {
      out[entry.depName] = String(entry.depVersion ?? '')
    }
  }
  return out
}

export function readModuleSpecsStamp(nodeModules) {
  try {
    return JSON.parse(fs.readFileSync(path.join(nodeModules, MODULE_SPECS_STAMP), 'utf8'))
  } catch {
    return null
  }
}

export function writeModuleSpecsStamp(nodeModules, moduleDeps) {
  const specs = moduleSpecsObject(moduleDeps)
  const keys = Object.keys(specs).sort()
  fs.mkdirSync(nodeModules, { recursive: true })
  fs.writeFileSync(
    path.join(nodeModules, MODULE_SPECS_STAMP),
    `${JSON.stringify(specs, keys)}\n`,
    'utf8',
  )
}

export function isCoreTreeCurrent({ npmRoot, nodeModules }) {
  if (!fs.existsSync(nodeModules)) {
    return false
  }

  try {
    if (fs.readdirSync(nodeModules).length === 0) {
      return false
    }
  } catch {
    return false
  }

  for (const name of collectCoreDirectNames(npmRoot)) {
    if (!fs.existsSync(packageDir(nodeModules, name))) {
      return false
    }
  }

  const stampPath = coreTreeStampFile(nodeModules)
  if (!fs.existsSync(stampPath)) {
    return false
  }

  let stamped
  try {
    stamped = fs.readFileSync(stampPath, 'utf8').trim()
  } catch {
    return false
  }

  return stamped === lockfileFingerprint(npmRoot)
}
