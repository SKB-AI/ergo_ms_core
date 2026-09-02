"""Установка OS-службы API/worker/beat модуля без хардкода имён в ядре.

Использование: ergoms install-module-service --module=<name> --kind=api|worker|beat
               ergoms uninstall-module-service --module=<name> --kind=api|worker|beat
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from project_layout import ensure_dir  # noqa: E402
from sh_io import write_sh  # noqa: E402

WRAPPERS = PROJECT_ROOT / 'core' / 'deployment' / 'wrappers'


def _safe(name: str) -> str:
    return ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in name)


def _service_name(module: str, kind: str) -> str:
    from service_names import names_from_root

    return names_from_root(PROJECT_ROOT).module(module, kind)


def _write_wrappers(module: str, kind: str) -> tuple[Path, Path]:
    ensure_dir(WRAPPERS)
    safe = _safe(module)
    bat = WRAPPERS / f'start_module_{safe}_{kind}.bat'
    sh_path = WRAPPERS / f'start_module_{safe}_{kind}.sh'
    py = PROJECT_ROOT / 'virtual_env' / 'python'
    if kind == 'api':
        script = 'core\\api\\scripts\\start_module_api.py' if os.name == 'nt' else 'core/api/scripts/start_module_api.py'
        extra = f'--module={module}'
    elif kind == 'beat':
        script = 'core\\api\\scripts\\start_celery_beat.py' if os.name == 'nt' else 'core/api/scripts/start_celery_beat.py'
        extra = f'--module={module}'
    else:
        script = 'core\\api\\scripts\\start_celery_worker.py' if os.name == 'nt' else 'core/api/scripts/start_celery_worker.py'
        extra = f'--module={module}'
    bat.write_text(
        '@echo off\r\n'
        f'cd /d "{PROJECT_ROOT}"\r\n'
        f'"{py / "Scripts" / "python.exe"}" {script} {extra}\r\n',
        encoding='utf-8',
    )
    write_sh(
        sh_path,
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        f'cd "{PROJECT_ROOT}"\n'
        f'"{py / "bin" / "python"}" {script.replace(chr(92), "/")} {extra}\n',
    )
    return bat, sh_path


def _write_systemd_unit(module: str, kind: str, sh_path: Path) -> Path:
    unit_dir = WRAPPERS / 'systemd'
    ensure_dir(unit_dir)
    name = _service_name(module, kind)
    unit = unit_dir / f'{name}.service'
    stderr = PROJECT_ROOT / 'logs' / f'{name}.stderr.log'
    env_file = WRAPPERS / 'ergo_ms.env'
    content = (
        '[Unit]\n'
        f'Description=ERGO MS module {kind} ({module})\n'
        'After=network.target\n'
        '\n'
        '[Service]\n'
        'Type=simple\n'
        f'EnvironmentFile=-{env_file}\n'
        f'ExecStart=/bin/bash "{sh_path}"\n'
        'Restart=always\n'
        'RestartSec=5\n'
        'TimeoutStopSec=30\n'
        'Environment=PYTHONUNBUFFERED=1\n'
        'Environment=ERGO_LOG_CONSOLE=false\n'
        'StandardOutput=null\n'
        f'StandardError=append:{stderr}\n'
        '\n'
        '[Install]\n'
        'WantedBy=multi-user.target\n'
    )
    unit.write_text(content, encoding='utf-8')
    return unit


def _install_linux(module: str, kind: str, sh_path: Path) -> int:
    unit = _write_systemd_unit(module, kind, sh_path)
    name = _service_name(module, kind)
    print(format_console('info', t('module_service_unit_written', path=unit)))
    link = subprocess.call(['systemctl', 'link', str(unit)])
    if link != 0:
        print(format_console('warning', t('module_service_systemctl_hint', unit=name)))
        return 0
    subprocess.call(['systemctl', 'enable', '--now', name])
    return 0


def _nssm_exe() -> Path | None:
    candidate = PROJECT_ROOT / 'virtual_env' / 'packages' / 'nssm' / 'win64' / 'nssm.exe'
    if candidate.is_file():
        return candidate
    alt = PROJECT_ROOT / 'virtual_env' / 'packages' / 'nssm' / 'nssm.exe'
    return alt if alt.is_file() else None


def _install_windows(module: str, kind: str, bat: Path) -> int:
    nssm = _nssm_exe()
    name = _service_name(module, kind)
    if nssm is None:
        print(format_console('warning', t('module_service_nssm_missing', wrapper=bat, name=name)))
        return 0
    subprocess.call([str(nssm), 'install', name, str(bat)])
    subprocess.call([str(nssm), 'set', name, 'AppDirectory', str(PROJECT_ROOT)])
    subprocess.call([str(nssm), 'start', name])
    print(format_console('ok', t('module_service_installed', name=name)))
    return 0


def _windows_service_exists(name: str) -> bool:
    result = subprocess.run(['sc', 'query', name], capture_output=True, check=False)
    return result.returncode == 0


def _linux_unit_exists(name: str) -> bool:
    unit = name if name.endswith('.service') else f'{name}.service'
    path = Path('/etc/systemd/system') / unit
    if path.is_file() or path.is_symlink():
        return True
    result = subprocess.run(
        ['systemctl', 'list-unit-files', '--type=service', '--no-legend', unit],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool((result.stdout or '').strip())


def _uninstall_windows(name: str) -> int:
    if not _windows_service_exists(name):
        return 0
    nssm = _nssm_exe()
    if nssm is None:
        print(format_console('skip', t('module_service_nssm_missing', wrapper='-', name=name)))
        return 0
    subprocess.call([str(nssm), 'stop', name])
    subprocess.call([str(nssm), 'remove', name, 'confirm'])
    return 0


def _uninstall_linux(name: str) -> int:
    if not _linux_unit_exists(name):
        return 0
    subprocess.call(['systemctl', 'disable', '--now', name])
    return 0


def main() -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description='Install or remove a module OS service')
    parser.add_argument('--module', required=True)
    parser.add_argument('--kind', choices=('api', 'worker', 'beat'), default='api')
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()
    module = args.module.strip()
    kind = args.kind
    if not (PROJECT_ROOT / 'modules' / module).is_dir():
        print(format_console('error', t('module_service_unknown_module', name=module)), file=sys.stderr)
        return 2
    name = _service_name(module, kind)
    if args.uninstall:
        if os.name == 'nt':
            return _uninstall_windows(name)
        return _uninstall_linux(name)
    from lifecycle.modules.catalog import ModuleCatalog  # noqa: WPS433

    catalog = ModuleCatalog.from_project_env(PROJECT_ROOT)
    if not catalog.allows_module_process_os_services(module):
        print(format_console('skip', t('module_process_service_skip_runtime', name=module)))
        return 0
    bat, sh_path = _write_wrappers(module, kind)
    if os.name == 'nt':
        return _install_windows(module, kind, bat)
    return _install_linux(module, kind, sh_path)


if __name__ == '__main__':
    raise SystemExit(main())
