"""
Установка и управление portable Meilisearch в virtual_env/packages/meilisearch.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
SCRIPTS_DIR = DEPLOYMENT_DIR / 'scripts'
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from console_tags import configure_stdio_utf8  # noqa: E402
from env_file_loader import load_project_env  # noqa: E402
from project_layout import (  # noqa: E402
    meilisearch_data_dir as _layout_meilisearch_data_dir,
    meilisearch_runtime_dir as _layout_meilisearch_runtime_dir,
    package_dir,
)

MEILISEARCH_VERSION = '1.43.1'
DEFAULT_PORT = 8004
DEFAULT_BIND = '127.0.0.1'
DOWNLOAD_USER_AGENT = 'ergoms/1.0 (Meilisearch installer)'
DOWNLOAD_TIMEOUT_SEC = 300

RELEASE_BASE = (
    f'https://github.com/meilisearch/meilisearch/releases/download/v{MEILISEARCH_VERSION}'
)


def meilisearch_packages_dir(root: Path) -> Path:
    return package_dir(root, 'meilisearch')


def meilisearch_runtime_dir(root: Path) -> Path:
    return _layout_meilisearch_runtime_dir(root)


def meilisearch_data_dir(root: Path) -> Path:
    """Каталог LMDB Meilisearch (индексы в cache)."""
    return _layout_meilisearch_data_dir(root)


def meilisearch_binary_path(root: Path) -> Path:
    base = meilisearch_packages_dir(root)
    if platform.system().lower() == 'windows':
        return base / 'meilisearch.exe'
    return base / 'meilisearch'


def meilisearch_pid_file(root: Path) -> Path:
    return meilisearch_runtime_dir(root) / 'meilisearch.pid'


def meilisearch_log_file(root: Path) -> Path:
    return root / 'logs' / 'meilisearch.log'


def _download_url() -> tuple[str, str]:
    if platform.system().lower() == 'windows':
        return (
            f'{RELEASE_BASE}/meilisearch-windows-amd64.exe',
            'meilisearch.exe',
        )
    return (
        f'{RELEASE_BASE}/meilisearch-linux-amd64',
        'meilisearch',
    )


def is_installed(root: Path) -> bool:
    return meilisearch_binary_path(root).is_file()


def _download_once(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={'User-Agent': DOWNLOAD_USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SEC) as response:
        with open(destination, 'wb') as out:
            while True:
                chunk = response.read(8 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    if destination.stat().st_size < 1024:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f'Слишком маленький файл загрузки: {url}')


def _download_with_curl(url: str, destination: Path) -> bool:
    curl_exe = shutil.which('curl')
    if not curl_exe:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                curl_exe,
                '-L',
                '--fail',
                '--retry',
                '3',
                '--retry-delay',
                '2',
                '-A',
                DOWNLOAD_USER_AGENT,
                '-o',
                str(destination),
                url,
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return (
        result.returncode == 0
        and destination.is_file()
        and destination.stat().st_size >= 1024
    )


def _download(url: str, destination: Path) -> None:
    last_error: Exception | None = None
    attempts = 3
    for attempt in range(1, attempts + 1):
        print(f'[INFO] Загрузка ({attempt}/{attempts}): {url}')
        try:
            _download_once(url, destination)
            return
        except Exception as exc:
            last_error = exc
            print(f'[WARNING] urllib download failed ({exc}); trying curl...')
            destination.unlink(missing_ok=True)

        if _download_with_curl(url, destination):
            return

        if attempt < attempts:
            time.sleep(2 * attempt)
            destination.unlink(missing_ok=True)

    raise RuntimeError(
        f'Не удалось скачать Meilisearch. Последняя ошибка: {last_error}'
    ) from last_error


def _read_dotenv_value(root: Path, name: str) -> str | None:
    """Значение из .env + env/*.env (фрагменты перекрывают корень, как load_project_env)."""
    merged = load_project_env(root)
    value = merged.get(name)
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped != '' else None


def _resolve_env(root: Path, name: str, default: str, *, allow_os: bool = False) -> str:
    """Приоритет: .env + env/*.env → (опционально os.environ) → default."""
    from_file = _read_dotenv_value(root, name)
    if from_file is not None and from_file != '':
        return from_file
    if allow_os:
        return os.environ.get(name, default) or default
    return default


_TEMPLATE_MASTER_KEY = 'ergo_ms_dev_meili_key'


def _resolve_master_key(root: Path) -> str:
    return _resolve_env(root, 'MEILI_MASTER_KEY', '', allow_os=True)


def _is_insecure_master_key(key: str) -> bool:
    stripped = (key or '').strip()
    return (not stripped) or stripped.lower() == _TEMPLATE_MASTER_KEY


def _ergo_env(root: Path) -> str:
    return _resolve_env(root, 'ERGO_ENV', 'development', allow_os=True).strip().lower()


def _host_to_http_addr(host: str) -> str:
    value = host.strip().rstrip('/')
    for prefix in ('http://', 'https://'):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
            break
    return value or f'{DEFAULT_BIND}:{DEFAULT_PORT}'


def _runtime_settings(root: Path) -> dict[str, str]:
    # Порт/host не берём из shell-env: иначе залипший MEILI_HTTP_ADDR=7700 бьёт default 8004.
    host = _resolve_env(
        root, 'MEILI_HOST', f'http://{DEFAULT_BIND}:{DEFAULT_PORT}'
    ).rstrip('/')
    http_addr = _resolve_env(root, 'MEILI_HTTP_ADDR', '')
    if not http_addr:
        http_addr = _host_to_http_addr(host)
    return {
        'env': _resolve_env(root, 'MEILI_ENV', 'development', allow_os=True),
        'http_addr': http_addr,
        'host': host,
        'db_path': str(meilisearch_data_dir(root)),
        'master_key': _resolve_master_key(root),
    }


def _build_env(root: Path) -> dict[str, str]:
    settings = _runtime_settings(root)
    env = os.environ.copy()
    env['MEILI_ENV'] = settings['env']
    env['MEILI_HTTP_ADDR'] = settings['http_addr']
    env['MEILI_HOST'] = settings['host']
    env['MEILI_DB_PATH'] = settings['db_path']
    env['MEILI_MASTER_KEY'] = settings['master_key']
    env['MEILI_NO_ANALYTICS'] = 'true'
    return env


def _build_argv(root: Path) -> list[str]:
    settings = _runtime_settings(root)
    return [
        str(meilisearch_binary_path(root)),
        '--db-path',
        settings['db_path'],
        '--http-addr',
        settings['http_addr'],
        '--env',
        settings['env'],
        '--master-key',
        settings['master_key'],
        '--no-analytics',
    ]


def _health_url(root: Path) -> str:
    return f"{_runtime_settings(root)['host']}/health"


def ping_meilisearch(root: Path) -> bool:
    try:
        request = urllib.request.Request(_health_url(root))
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


def _installed_version(root: Path) -> str | None:
    binary = meilisearch_binary_path(root)
    if not binary.is_file():
        return None
    try:
        result = subprocess.run(
            [str(binary), '--version'],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = f'{result.stdout or ""} {result.stderr or ""}'
    match = re.search(r'(\d+\.\d+\.\d+)', text)
    return match.group(1) if match else None


def cmd_install(root: Path) -> int:
    current = _installed_version(root)
    if current == MEILISEARCH_VERSION:
        print(f'[OK] Meilisearch уже установлен: {meilisearch_binary_path(root)}')
        return 0
    if current:
        print(
            f'[INFO] Обновление Meilisearch {current} → {MEILISEARCH_VERSION}. '
            'После замены бинарника выполните ergoms search-reindex; '
            'переход 1.12→1.43 может потребовать переиндексации с нуля.'
        )

    url, filename = _download_url()
    target_dir = meilisearch_packages_dir(root)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    print(f'[INFO] Загрузка Meilisearch {MEILISEARCH_VERSION}...')
    try:
        from download_cache import download_with_cache

        download_with_cache(
            root,
            'meilisearch',
            target,
            lambda dest: _download(url, dest),
            filename=f'{MEILISEARCH_VERSION}-{filename}',
        )
    except Exception as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 1
    if platform.system().lower() != 'windows':
        target.chmod(target.stat().st_mode | 0o111)
    meilisearch_data_dir(root).mkdir(parents=True, exist_ok=True)
    print(f'[OK] Meilisearch установлен: {target}')
    return 0


def _read_pid(root: Path) -> int | None:
    pid_file = meilisearch_pid_file(root)
    if not pid_file.is_file():
        return None
    try:
        return int(pid_file.read_text(encoding='utf-8').strip())
    except (TypeError, ValueError):
        return None


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system().lower() == 'windows':
        result = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
        return str(pid) in (result.stdout or '')
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_start(root: Path) -> int:
    if not is_installed(root):
        print('[ERROR] Meilisearch не установлен. Запустите: ergoms install-meilisearch', file=sys.stderr)
        return 1
    if ping_meilisearch(root):
        print('[OK] Meilisearch уже отвечает')
        return 0

    pid = _read_pid(root)
    if pid and _is_pid_running(pid):
        if ping_meilisearch(root):
            print('[OK] Meilisearch уже запущен')
            return 0

    meilisearch_runtime_dir(root).mkdir(parents=True, exist_ok=True)
    meilisearch_data_dir(root).mkdir(parents=True, exist_ok=True)
    log_path = meilisearch_log_file(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    from security.ensure_secret import ensure_mode_secrets_for_process

    ensure_mode_secrets_for_process(root)

    master_key = _resolve_master_key(root)
    if _is_insecure_master_key(master_key) and _ergo_env(root) == 'production':
        print(
            '[ERROR] MEILI_MASTER_KEY пуст или из шаблона. '
            'Задайте ключ через ergoms generate-secret или env/search.env.',
            file=sys.stderr,
        )
        return 1

    env = _build_env(root)
    log_handle = open(log_path, 'a', encoding='utf-8')  # noqa: SIM115
    creationflags = 0
    if platform.system().lower() == 'windows':
        # CREATE_NO_WINDOW надёжнее передаёт env, чем DETACHED_PROCESS.
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        )

    process = subprocess.Popen(
        _build_argv(root),
        cwd=str(meilisearch_runtime_dir(root)),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    meilisearch_pid_file(root).write_text(str(process.pid), encoding='utf-8')

    for _ in range(30):
        if ping_meilisearch(root):
            print(f'[OK] Meilisearch запущен (pid={process.pid})')
            return 0
        time.sleep(0.5)

    print('[ERROR] Meilisearch не ответил на health check', file=sys.stderr)
    print(f'[INFO] Проверьте лог: {log_path}', file=sys.stderr)
    return 1


def cmd_stop(root: Path) -> int:
    pid = _read_pid(root)
    if not pid or not _is_pid_running(pid):
        meilisearch_pid_file(root).unlink(missing_ok=True)
        print('[OK] Meilisearch не запущен')
        return 0

    if platform.system().lower() == 'windows':
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], check=False)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    meilisearch_pid_file(root).unlink(missing_ok=True)
    print('[OK] Meilisearch остановлен')
    return 0


def cmd_test(root: Path) -> int:
    if not is_installed(root):
        print('[ERROR] Meilisearch не установлен', file=sys.stderr)
        return 1
    if ping_meilisearch(root):
        print('[OK] Meilisearch health OK')
        return 0
    print('[ERROR] Meilisearch недоступен', file=sys.stderr)
    return 1


def cmd_status(root: Path) -> int:
    print('')
    print('Meilisearch')
    if not is_installed(root):
        print('  Status: not installed')
        print(f'  Expected: {meilisearch_packages_dir(root)}')
        return 0
    pid = _read_pid(root)
    if ping_meilisearch(root):
        print(f'  Status: running (pid={pid or "external"})')
    elif pid and _is_pid_running(pid):
        print(f'  Status: process running, health failed (pid={pid})')
    else:
        print('  Status: not running')
    print(f'  Binary: {meilisearch_binary_path(root)}')
    print(f'  Data: {meilisearch_data_dir(root)}')
    print(f'  Health: {_health_url(root)}')
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description='Portable Meilisearch для ERGO MS')
    parser.add_argument(
        'operation',
        choices=('install', 'start', 'stop', 'restart', 'test', 'status'),
    )
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()

    if args.operation == 'install':
        return cmd_install(root)
    if args.operation == 'start':
        return cmd_start(root)
    if args.operation == 'stop':
        return cmd_stop(root)
    if args.operation == 'restart':
        code = cmd_stop(root)
        if code != 0:
            return code
        return cmd_start(root)
    if args.operation == 'test':
        return cmd_test(root)
    return cmd_status(root)


if __name__ == '__main__':
    raise SystemExit(main())
