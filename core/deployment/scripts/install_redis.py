"""
Установка portable Redis в virtual_env/packages/redis (Windows zip / Linux source build).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_DIR = PROJECT_ROOT / 'core' / 'deployment'
SCRIPTS_DIR = DEPLOYMENT_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))
TEMPLATE_PATH = DEPLOYMENT_DIR / 'redis' / 'redis.conf.template'

REDIS_LINUX_VERSION = '7.4.2'
REDIS_LINUX_TARBALL = f'redis-{REDIS_LINUX_VERSION}.tar.gz'
REDIS_LINUX_URL = f'https://download.redis.io/releases/{REDIS_LINUX_TARBALL}'
REDIS_LINUX_FALLBACK_URL = (
    f'https://codeload.github.com/redis/redis/tar.gz/refs/tags/{REDIS_LINUX_VERSION}'
)

DOWNLOAD_USER_AGENT = 'ergoms/1.0 (Redis installer)'
DOWNLOAD_TIMEOUT_SEC = 300

REDIS_WINDOWS_VERSION = '7.4.9'
REDIS_WINDOWS_ZIP = f'Redis-{REDIS_WINDOWS_VERSION}-Windows-x64-msys2.zip'
REDIS_WINDOWS_URL = (
    f'https://github.com/redis-windows/redis-windows/releases/download/'
    f'{REDIS_WINDOWS_VERSION}/{REDIS_WINDOWS_ZIP}'
)

DEFAULT_PORT = 6379
DEFAULT_BIND = '127.0.0.1'


def _parse_simple_yaml_section(text: str, section: str) -> dict[str, str]:
    """Минимальный разбор секции databases.yaml без PyYAML."""
    lines = text.splitlines()
    in_databases = False
    in_section = False
    section_indent = -1
    result: dict[str, str] = {}
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        stripped = raw.strip()
        if stripped == 'databases:':
            in_databases = True
            in_section = False
            continue
        if not in_databases:
            continue
        if indent == 2 and stripped.endswith(':'):
            name = stripped[:-1].strip()
            in_section = name == section
            section_indent = indent
            continue
        if in_section and indent > section_indent and ':' in stripped:
            key, _, value = stripped.partition(':')
            value = value.strip().strip('"').strip("'")
            result[key.strip()] = value
        elif in_section and indent <= section_indent and stripped.endswith(':'):
            break
    return result


def load_redis_password(root: Path) -> str:
    """Пароль из databases.yaml → redis.password (SoT); пусто = без AUTH."""
    path = root / 'databases.yaml'
    if not path.is_file():
        return ''
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return ''
    section = _parse_simple_yaml_section(text, 'redis')
    return (section.get('password') or '').strip()


def load_redis_user(root: Path) -> str:
    """Имя ACL-пользователя из databases.yaml → redis.user; пусто = только default."""
    path = root / 'databases.yaml'
    if not path.is_file():
        return ''
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return ''
    section = _parse_simple_yaml_section(text, 'redis')
    return (section.get('user') or '').strip()


def format_requirepass_line(password: str) -> str:
    if not password:
        return '# requirepass unset (databases.yaml redis.password пуст)'
    if re.fullmatch(r'[A-Za-z0-9_\-./]+', password):
        return f'requirepass {password}'
    escaped = password.replace('\\', '\\\\').replace('"', '\\"')
    return f'requirepass "{escaped}"'


def format_acl_user_line(username: str, password: str) -> str:
    if not username or not password:
        return '# acl user unset (databases.yaml redis.user пуст)'
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', username):
        return '# acl user skipped (некорректный redis.user)'
    if re.fullmatch(r'[A-Za-z0-9_\-./]+', password):
        return f'user {username} on >{password} ~* &* +@all'
    escaped = password.replace('\\', '\\\\').replace('"', '\\"')
    return f'user {username} on >"{escaped}" ~* &* +@all'


def redis_cli_auth_args(root: Path) -> list[str]:
    password = load_redis_password(root)
    if not password:
        return []
    return ['-a', password, '--no-auth-warning']


def redis_packages_dir(root: Path) -> Path:
    return root / 'virtual_env' / 'packages' / 'redis'


def redis_server_path(root: Path) -> Path:
    base = redis_packages_dir(root)
    if platform.system().lower() == 'windows':
        candidate = base / 'redis-server.exe'
        if candidate.is_file():
            return candidate
        return base / 'bin' / 'redis-server.exe'
    return base / 'bin' / 'redis-server'


def redis_cli_path(root: Path) -> Path:
    base = redis_packages_dir(root)
    if platform.system().lower() == 'windows':
        candidate = base / 'redis-cli.exe'
        if candidate.is_file():
            return candidate
        return base / 'bin' / 'redis-cli.exe'
    return base / 'bin' / 'redis-cli'


def redis_conf_path(root: Path) -> Path:
    return redis_packages_dir(root) / 'conf' / 'redis.conf'


def is_installed(root: Path) -> bool:
    return redis_server_path(root).is_file() and redis_conf_path(root).is_file()


def _download_once(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={'User-Agent': DOWNLOAD_USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SEC) as response:
        destination.write_bytes(response.read())
    if destination.stat().st_size < 1024:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f'Downloaded file is too small: {url}')


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


def _download(url: str, destination: Path, *, fallback_urls: tuple[str, ...] = ()) -> None:
    last_error: Exception | None = None
    for candidate in (url, *fallback_urls):
        print(f'-> Downloading {candidate}')
        try:
            _download_once(candidate, destination)
            return
        except Exception as exc:
            last_error = exc
            print(f'[ergoms] Download failed ({exc}); trying next source...')
            destination.unlink(missing_ok=True)

        if _download_with_curl(candidate, destination):
            return

    raise RuntimeError(
        f'Could not download Redis archive. Last error: {last_error}'
    ) from last_error


def _ensure_layout(root: Path) -> dict[str, Path]:
    base = redis_packages_dir(root)
    paths = {
        'base': base,
        'conf': base / 'conf',
        'data': base / 'data',
        'logs': base / 'logs',
        'run': base / 'run',
        'bin': base / 'bin',
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _windows_redis_version(root: Path) -> str | None:
    server = redis_server_path(root)
    if not server.is_file():
        return None
    try:
        result = subprocess.run(
            [str(server), '--version'],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    match = re.search(r'v=(\d+\.\d+\.\d+)', (result.stdout or '') + (result.stderr or ''))
    return match.group(1) if match else None


def _find_windows_redis_source(extract_dir: Path) -> Path:
    for pattern in ('Redis-*-Windows-x64-msys2', 'Redis-x64-*'):
        nested = sorted(extract_dir.glob(pattern))
        if nested:
            return nested[0]
    return extract_dir


def _copy_windows_redis_binaries(source_root: Path, dest: Path) -> None:
    copied = 0
    for pattern in ('*.exe', '*.dll'):
        for src in source_root.glob(pattern):
            shutil.copy2(src, dest / src.name)
            copied += 1
    if copied == 0:
        raise RuntimeError(f'Expected Redis binaries in Windows archive: {source_root}')


def render_redis_conf(
    root: Path,
    port: int = DEFAULT_PORT,
    bind: str = DEFAULT_BIND,
    password: str | None = None,
) -> Path:
    from log_env import log_file_path, redis_log_level
    from logs_paths import ensure_logs_dir

    paths = _ensure_layout(root)
    template = TEMPLATE_PATH.read_text(encoding='utf-8')
    conf_path = paths['conf'] / 'redis.conf'
    central_log = log_file_path('REDIS', root)
    ensure_logs_dir(root)
    log_level = redis_log_level(root)
    effective_password = password if password is not None else load_redis_password(root)
    requirepass_line = format_requirepass_line(effective_password)
    acl_user_line = format_acl_user_line(load_redis_user(root), effective_password)

    if platform.system().lower() == 'windows':
        pidfile = (paths['run'] / 'redis.pid').as_posix()
        logfile = central_log.as_posix()
        data_dir = paths['data'].as_posix()
        daemonize = 'no'
    else:
        pidfile = str(paths['run'] / 'redis.pid')
        logfile = str(central_log)
        data_dir = str(paths['data'])
        daemonize = 'yes'

    content = (
        template.replace('{{REDIS_BIND}}', bind)
        .replace('{{REDIS_PORT}}', str(port))
        .replace('{{REDIS_REQUIREPASS_LINE}}', requirepass_line)
        .replace('{{REDIS_ACL_USER_LINE}}', acl_user_line)
        .replace('{{REDIS_DAEMONIZE}}', daemonize)
        .replace('{{REDIS_PIDFILE}}', pidfile)
        .replace('{{REDIS_LOGFILE}}', logfile)
        .replace('{{REDIS_LOGLEVEL}}', log_level)
        .replace('{{REDIS_DATA_DIR}}', data_dir)
    )
    from lifecycle.host.privilege import restore_project_ownership

    try:
        conf_path.write_text(content, encoding='utf-8')
    except PermissionError:
        if not restore_project_ownership(root, paths['base']):
            raise
        conf_path.write_text(content, encoding='utf-8')
    restore_project_ownership(root, paths['base'])
    return conf_path


def _install_windows(root: Path, force: bool) -> None:
    paths = _ensure_layout(root)
    server = redis_server_path(root)
    installed_version = _windows_redis_version(root) if server.is_file() else None
    if installed_version and not force:
        if installed_version == REDIS_WINDOWS_VERSION:
            print('[ergoms] Redis already installed (use --force to reinstall)')
            return
        print(
            f'[ergoms] Upgrading Redis {installed_version} -> {REDIS_WINDOWS_VERSION}...'
        )
        force = True

    if force and paths['base'].exists():
        for target in paths['base'].iterdir():
            if target.is_file() and target.suffix.lower() in {'.exe', '.dll'}:
                target.unlink()
        bin_dir = paths['base'] / 'bin'
        if bin_dir.is_dir():
            shutil.rmtree(bin_dir, ignore_errors=True)

    cache_tmp = root / 'virtual_env' / 'cache' / 'tmp'
    cache_tmp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(cache_tmp)) as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / REDIS_WINDOWS_ZIP
        from download_cache import download_with_cache

        download_with_cache(
            root,
            'redis',
            zip_path,
            lambda dest: _download(REDIS_WINDOWS_URL, dest),
        )
        extract_dir = tmp_path / 'extract'
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, 'r') as archive:
            archive.extractall(extract_dir)

        source_root = _find_windows_redis_source(extract_dir)
        _copy_windows_redis_binaries(source_root, paths['base'])

    print(
        f'[ergoms] Redis {REDIS_WINDOWS_VERSION} (Windows, redis-windows/msys2) '
        f'installed to {paths["base"]}'
    )


def _install_linux(root: Path, force: bool) -> None:
    paths = _ensure_layout(root)
    server = redis_server_path(root)
    if server.is_file() and not force:
        print('[ergoms] Redis already installed (use --force to reinstall)')
        return

    from linux_build_packages import ensure_linux_build_packages

    ensure_linux_build_packages('redis')

    prefix = paths['base']
    if force:
        bin_dir = prefix / 'bin'
        if bin_dir.is_dir():
            shutil.rmtree(bin_dir, ignore_errors=True)

    cache_tmp = root / 'virtual_env' / 'cache' / 'tmp'
    cache_tmp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(cache_tmp)) as tmp:
        tmp_path = Path(tmp)
        tar_path = tmp_path / REDIS_LINUX_TARBALL
        from download_cache import download_with_cache

        download_with_cache(
            root,
            'redis',
            tar_path,
            lambda dest: _download(
                REDIS_LINUX_URL,
                dest,
                fallback_urls=(REDIS_LINUX_FALLBACK_URL,),
            ),
        )
        extract_dir = tmp_path / 'src'
        extract_dir.mkdir()
        with tarfile.open(tar_path, 'r:gz') as archive:
            archive.extractall(extract_dir)

        source_dirs = list(extract_dir.glob(f'redis-{REDIS_LINUX_VERSION}'))
        if not source_dirs:
            source_dirs = [p for p in extract_dir.iterdir() if p.is_dir() and p.name.startswith('redis-')]
        if not source_dirs:
            raise RuntimeError('Could not find extracted Redis source directory')
        source_dir = source_dirs[0]

        print(f'-> Building Redis {REDIS_LINUX_VERSION} (this may take 1–3 minutes)...')
        subprocess.run(['make', '-C', str(source_dir), '-j', str(max(1, (os.cpu_count() or 2)))],
                       check=True)
        subprocess.run(
            ['make', '-C', str(source_dir), f'PREFIX={prefix}', 'install'],
            check=True,
        )

    print(f'[ergoms] Redis {REDIS_LINUX_VERSION} installed to {prefix}')


def install_redis(
    root: Path,
    *,
    port: int = DEFAULT_PORT,
    force: bool = False,
    platform_name: str = 'auto',
) -> None:
    system = platform_name.lower()
    if system == 'auto':
        system = platform.system().lower()

    from security.ensure_infra_credentials import ensure_infra_credentials

    ensure_infra_credentials(root)

    if system == 'windows':
        _install_windows(root, force)
    elif system == 'linux':
        _install_linux(root, force)
    else:
        raise RuntimeError(f'Unsupported platform for portable Redis: {system}')

    conf = render_redis_conf(root, port=port)
    print(f'[ergoms] Redis config: {conf}')


def _redis_connect_host(bind: str) -> str:
    if bind in ('0.0.0.0', '*', '::', '::0'):
        return DEFAULT_BIND
    return bind


def redis_endpoint(root: Path, port: int | None = None) -> tuple[str, int]:
    bind = DEFAULT_BIND
    resolved_port = port or DEFAULT_PORT
    conf = redis_conf_path(root)
    if conf.is_file() and port is None:
        conf_text = conf.read_text(encoding='utf-8')
        match = re.search(r'^port\s+(\d+)\s*$', conf_text, re.MULTILINE)
        if match:
            resolved_port = int(match.group(1))
        bind_match = re.search(r'^bind\s+(\S+)', conf_text, re.MULTILINE)
        if bind_match:
            bind = bind_match.group(1)
    return _redis_connect_host(bind), resolved_port


def _resp_command(*parts: str) -> bytes:
    chunks = [f'*{len(parts)}\r\n'.encode('ascii')]
    for part in parts:
        data = part.encode('utf-8')
        chunks.append(f'${len(data)}\r\n'.encode('ascii'))
        chunks.append(data)
        chunks.append(b'\r\n')
    return b''.join(chunks)


def _recv_redis_line(sock: socket.socket) -> bytes:
    buf = bytearray()
    while True:
        chunk = sock.recv(256)
        if not chunk:
            break
        buf.extend(chunk)
        if b'\r\n' in buf:
            break
    idx = buf.find(b'\r\n')
    if idx < 0:
        return bytes(buf)
    return bytes(buf[: idx + 2])


def ping_redis(root: Path, port: int | None = None, timeout_sec: float = 0.4) -> bool:
    """AUTH+PING по TCP. Не вызывает redis-cli: MSYS2-сборка на Windows стартует секунды."""
    host, resolved_port = redis_endpoint(root, port)
    password = load_redis_password(root)
    try:
        with socket.create_connection((host, resolved_port), timeout=timeout_sec) as sock:
            sock.settimeout(timeout_sec)
            if password:
                sock.sendall(_resp_command('AUTH', password))
                if not _recv_redis_line(sock).startswith(b'+OK'):
                    return False
            sock.sendall(_resp_command('PING'))
            return b'PONG' in _recv_redis_line(sock)
    except OSError:
        return False


def wait_redis_ready(root: Path, timeout_sec: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if ping_redis(root, timeout_sec=0.2):
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description='Install portable Redis into virtual_env/packages/redis')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--platform', default='auto', choices=('auto', 'windows', 'linux'))
    parser.add_argument('--ping-only', action='store_true', help='Ping Redis over TCP (RESP)')
    parser.add_argument('--wait-ready', action='store_true', help='Poll ping until Redis accepts AUTH')
    parser.add_argument('--wait-timeout', type=float, default=8.0)
    args = parser.parse_args()

    if args.ping_only:
        return 0 if ping_redis(args.root) else 1
    if args.wait_ready:
        return 0 if wait_redis_ready(args.root, timeout_sec=args.wait_timeout) else 1

    install_redis(args.root, port=args.port, force=args.force, platform_name=args.platform)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
