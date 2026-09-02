/**
 * Проверяет, что package-lock.json не содержит модулей и их npm-зависимостей.
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const NPM_ROOT = path.join(ROOT, 'virtual_env', 'npm')
const LOCK_PATH = path.join(NPM_ROOT, 'package-lock.json')
const PACKAGE_JSON_PATH = path.join(NPM_ROOT, 'package.json')
const MODULES_ROOT = path.join(ROOT, 'modules')

const ALLOWED_ERGO_MS_PACKAGES = new Set(['@ergo-ms/core-client'])

function normalizePackageName(name) {
  return name.trim().toLowerCase()
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'))
}

function collectRootDependencyNames(packageJson) {
  const names = new Set()
  for (const section of ['dependencies', 'devDependencies']) {
    const deps = packageJson[section] ?? {}
    for (const depName of Object.keys(deps)) {
      names.add(normalizePackageName(depName))
    }
  }
  return names
}

function collectModuleExclusiveNpmDeps(rootDeps) {
  const exclusive = new Set()

  if (!fs.existsSync(MODULES_ROOT)) {
    return exclusive
  }

  for (const dirent of fs.readdirSync(MODULES_ROOT, { withFileTypes: true })) {
    if (!dirent.isDirectory()) {
      continue
    }

    const packageJsonPath = path.join(MODULES_ROOT, dirent.name, 'client', 'package.json')
    if (!fs.existsSync(packageJsonPath)) {
      continue
    }

    const pkg = readJson(packageJsonPath)
    const dependencies = pkg.dependencies ?? {}
    for (const depName of Object.keys(dependencies)) {
      const normalized = normalizePackageName(depName)
      if (!rootDeps.has(normalized)) {
        exclusive.add(normalized)
      }
    }
  }

  return exclusive
}

function collectLockPackageNames(lockData) {
  const names = new Set()

  const packages = lockData.packages ?? {}
  for (const pkgMeta of Object.values(packages)) {
    if (pkgMeta && typeof pkgMeta === 'object' && typeof pkgMeta.name === 'string') {
      names.add(normalizePackageName(pkgMeta.name))
    }
  }

  for (const key of Object.keys(packages)) {
    if (!key.startsWith('node_modules/')) {
      continue
    }
    const segments = key.slice('node_modules/'.length).split('/node_modules/')
    const depName = segments[segments.length - 1]
    if (depName.startsWith('@')) {
      const scopedParts = depName.split('/')
      if (scopedParts.length >= 2) {
        names.add(normalizePackageName(`${scopedParts[0]}/${scopedParts[1]}`))
      }
    } else {
      names.add(normalizePackageName(depName))
    }
  }

  return names
}

function findForbiddenErgoMsPackages(lockContent) {
  const found = new Set()
  const pattern = /"@ergo-ms\/[^"]+"/g
  let match = pattern.exec(lockContent)
  while (match) {
    const value = match[0].slice(1, -1)
    if (!ALLOWED_ERGO_MS_PACKAGES.has(value)) {
      found.add(value)
    }
    match = pattern.exec(lockContent)
  }
  return [...found].sort()
}

function findModuleWorkspacePaths(lockData) {
  const found = new Set()

  function visit(value) {
    if (typeof value === 'string') {
      if (/(^|[\\/])modules[\\/]/.test(value)) {
        found.add(value)
      }
      return
    }
    if (Array.isArray(value)) {
      value.forEach(visit)
      return
    }
    if (value && typeof value === 'object') {
      Object.keys(value).forEach(visit)
      Object.values(value).forEach(visit)
    }
  }

  visit(lockData)
  return [...found].sort()
}

function main() {
  if (!fs.existsSync(LOCK_PATH)) {
    console.error('[lock-check] package-lock.json не найден в virtual_env/npm.')
    process.exit(1)
  }

  if (!fs.existsSync(PACKAGE_JSON_PATH)) {
    console.error('[lock-check] package.json не найден в virtual_env/npm.')
    process.exit(1)
  }

  const packageJson = readJson(PACKAGE_JSON_PATH)
  const rootDeps = collectRootDependencyNames(packageJson)
  const moduleExclusiveDeps = collectModuleExclusiveNpmDeps(rootDeps)
  const lockContent = fs.readFileSync(LOCK_PATH, 'utf8')
  const lockData = readJson(LOCK_PATH)
  const errors = []

  const rootWorkspaces = lockData.packages?.['']?.workspaces ?? []
  const leakedWorkspaces = rootWorkspaces.filter(
    (workspace) => workspace.includes('modules/') || workspace.includes('modules\\'),
  )
  if (leakedWorkspaces.length > 0) {
    errors.push(`в lock workspaces содержат модули: ${leakedWorkspaces.join(', ')}`)
  }

  const modulePaths = findModuleWorkspacePaths(lockData)
  if (modulePaths.length > 0) {
    errors.push(`найдены пути модулей в lock: ${modulePaths.join(', ')}`)
  }

  const forbiddenErgoMs = findForbiddenErgoMsPackages(lockContent)
  if (forbiddenErgoMs.length > 0) {
    errors.push(`найдены workspace-пакеты модулей: ${forbiddenErgoMs.join(', ')}`)
  }

  const lockPackageNames = collectLockPackageNames(lockData)
  const leakedModuleDeps = [...moduleExclusiveDeps].filter((dep) => lockPackageNames.has(dep))
  if (leakedModuleDeps.length > 0) {
    errors.push(`найдены модульные npm-пакеты в lock: ${leakedModuleDeps.join(', ')}`)
  }

  if (errors.length > 0) {
    console.error('[lock-check] package-lock.json содержит утечки модулей:')
    for (const error of errors) {
      console.error(`  - ${error}`)
    }
    process.exit(1)
  }

  console.log('[lock-check] package-lock.json: утечек модулей нет.')
}

main()
