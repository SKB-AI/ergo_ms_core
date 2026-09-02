"""Установка пакетов компилятора на Linux (apt / dnf / yum / pacman) через sudo."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402

_APT_LOCKS = (
    Path('/var/lib/dpkg/lock-frontend'),
    Path('/var/lib/dpkg/lock'),
    Path('/var/cache/apt/archives/lock'),
    Path('/var/lib/apt/lists/lock'),
)
_READLINE_HEADER = Path('/usr/include/readline/readline.h')

_PROFILES: dict[str, dict[str, list[str]]] = {
    'postgres': {
        'apt': [
            'build-essential',
            'libreadline-dev',
            'zlib1g-dev',
            'flex',
            'bison',
            'libxml2-dev',
            'libssl-dev',
            'liblz4-dev',
        ],
        'dnf': [
            'gcc',
            'make',
            'readline-devel',
            'zlib-devel',
            'flex',
            'bison',
            'libxml2-devel',
            'openssl-devel',
            'lz4-devel',
        ],
        'pacman': [
            'base-devel',
            'readline',
            'zlib',
            'openssl',
            'libxml2',
            'flex',
            'bison',
            'lz4',
        ],
    },
    'redis': {
        'apt': ['build-essential'],
        'dnf': ['gcc', 'make'],
        'pacman': ['base-devel'],
    },
}


def _privileged(argv: list[str]) -> int:
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        cmd = argv
    elif shutil.which('sudo'):
        cmd = ['sudo', *argv]
    else:
        return 127
    return subprocess.call(cmd)


def _apt_lock_busy() -> bool:
    if not shutil.which('fuser'):
        return False
    for lock in _APT_LOCKS:
        if not lock.exists():
            continue
        result = subprocess.run(['fuser', str(lock)], capture_output=True, check=False)
        if result.returncode == 0:
            return True
    return False


def _wait_for_apt_locks(timeout_sec: int = 180) -> None:
    started = time.monotonic()
    printed = False
    while _apt_lock_busy():
        if time.monotonic() - started >= timeout_sec:
            raise RuntimeError(t('linux_apt_lock_timeout'))
        if not printed:
            print(format_console('info', t('linux_waiting_apt_lock')))
            printed = True
        time.sleep(2)


def _detect_manager() -> str | None:
    for name in ('apt-get', 'dnf', 'yum', 'pacman'):
        if shutil.which(name):
            return name
    return None


def _profile_key(manager: str | None) -> str:
    if manager == 'apt-get':
        return 'apt'
    if manager in ('dnf', 'yum'):
        return 'dnf'
    if manager == 'pacman':
        return 'pacman'
    return 'apt'


def _install_command(manager: str, packages: list[str]) -> list[str]:
    if manager == 'apt-get':
        return [
            'apt-get',
            '-o',
            'DPkg::Lock::Timeout=120',
            '-o',
            'APT::Acquire::Retries=3',
            'install',
            '-y',
            '-qq',
            *packages,
        ]
    if manager in ('dnf', 'yum'):
        return [manager, 'install', '-y', '-q', *packages]
    return ['pacman', '-Sy', '--noconfirm', *packages]


def _manual_hint(manager: str | None, packages: list[str]) -> str:
    if manager == 'apt-get':
        return 'sudo apt-get install -y ' + ' '.join(packages)
    if manager in ('dnf', 'yum'):
        return f'sudo {manager} install -y ' + ' '.join(packages)
    if manager == 'pacman':
        return 'sudo pacman -Sy --noconfirm ' + ' '.join(packages)
    return t('linux_build_tools_generic')


def _missing_compiler_tools() -> list[str]:
    return [name for name in ('gcc', 'make') if shutil.which(name) is None]


def _already_ready(profile: str) -> bool:
    if _missing_compiler_tools():
        return False
    if profile == 'postgres':
        # gcc/make/readline уже могут быть от другой сборки — bison/flex нужны именно Postgres.
        return (
            _READLINE_HEADER.is_file()
            and shutil.which('bison') is not None
            and shutil.which('flex') is not None
        )
    return True


def ensure_linux_build_packages(profile: str) -> None:
    """Ставит компилятор и заголовки. Если пакеты уже есть — сразу выходит."""
    specs = _PROFILES[profile]
    if _already_ready(profile):
        return

    manager = _detect_manager()
    packages = specs[_profile_key(manager)]
    hint = _manual_hint(manager, packages)
    if manager is None:
        missing = _missing_compiler_tools()
        raise RuntimeError(
            t('linux_build_tools_missing', tools=', '.join(missing or ('gcc', 'make')), hint=hint)
        )

    print(format_console('info', t('linux_installing_build_packages')))
    if manager == 'apt-get':
        _wait_for_apt_locks()
    code = _privileged(_install_command(manager, packages))
    if code != 0:
        raise RuntimeError(t('linux_build_packages_failed', hint=hint))

    missing = _missing_compiler_tools()
    if missing:
        raise RuntimeError(t('linux_build_tools_missing', tools=', '.join(missing), hint=hint))
