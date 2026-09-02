/**
 * Запуск npm без DEP0190 (shell:true + массив args).
 * На Windows — через cmd.exe /d /s /c одной строкой; иначе spawn без shell.
 */

import { spawnSync } from 'node:child_process'

function quoteCmdArg(arg) {
  const s = String(arg)
  // ^ — escape-символ cmd.exe; удваиваем, чтобы дочерний процесс получил один ^.
  // Иначе pkg@^1.2.3 после кавычек превращается в битое имя пакета.
  const withCarets = s.replace(/\^/g, '^^')
  if (!/[ \t"&<>|()]/.test(s)) {
    return withCarets
  }
  return `"${withCarets.replace(/"/g, '""')}"`
}

export function runNpm(args, options = {}) {
  const base = {
    stdio: 'inherit',
    env: process.env,
    ...options,
    shell: false,
  }

  if (process.platform === 'win32') {
    const line = ['npm', ...args].map(quoteCmdArg).join(' ')
    return spawnSync('cmd.exe', ['/d', '/s', '/c', line], {
      ...base,
      windowsHide: true,
    })
  }

  return spawnSync('npm', args, base)
}
