"""Пути артефактов ERGO MS внутри корня проекта (без записи вне ERGO_ROOT)."""

from __future__ import annotations

import sys
from pathlib import Path


def virtual_env_dir(root: Path) -> Path:
    return root / 'virtual_env'


def packages_dir(root: Path) -> Path:
    return virtual_env_dir(root) / 'packages'


def package_dir(root: Path, name: str) -> Path:
    """Каталог одного portable-пакета: virtual_env/packages/<name>."""
    return packages_dir(root) / name


def cache_dir(root: Path) -> Path:
    return virtual_env_dir(root) / 'cache'


def huggingface_hub_cache_dir(root: Path) -> Path:
    """Кэш huggingface_hub (HF_HOME), не ~/.cache и не системный temp."""
    return cache_dir(root) / 'huggingface'


def huggingface_trained_models_dir(root: Path) -> Path:
    """Снимки весов Hugging Face: virtual_env/trained_models/huggingface/."""
    return virtual_env_dir(root) / 'trained_models' / 'huggingface'


def huggingface_snapshot_dir(root: Path, repo_id: str) -> Path:
    """Каталог весов org/name внутри huggingface_trained_models_dir."""
    parts = [
        part
        for part in (repo_id or '').strip().strip('/').split('/')
        if part and part not in ('.', '..')
    ]
    dest = huggingface_trained_models_dir(root)
    for part in parts:
        dest = dest / part
    return dest


_HF_LFS_PREFIX = b'version https://git-lfs'
_HF_WEIGHT_FILES = (
    'model.safetensors',
    'pytorch_model.bin',
)
_HF_WEIGHT_GLOBS = (
    '*.safetensors',
    'pytorch_model-*.bin',
)


def is_real_model_blob(path: Path, *, min_bytes: int = 1024) -> bool:
    """True, если файл — настоящие веса, а не указатель Git LFS."""
    if not path.is_file():
        return False
    try:
        if path.stat().st_size < min_bytes:
            return False
        with path.open('rb') as handle:
            head = handle.read(64)
    except OSError:
        return False
    return not head.startswith(_HF_LFS_PREFIX)


def huggingface_weight_files(path: Path) -> list[Path]:
    """Реальные файлы весов в каталоге снимка (safetensors или pytorch)."""
    if not path.is_dir():
        return []
    found: list[Path] = []
    seen: set[Path] = set()
    for name in _HF_WEIGHT_FILES:
        candidate = path / name
        if is_real_model_blob(candidate) and candidate not in seen:
            found.append(candidate)
            seen.add(candidate)
    for pattern in _HF_WEIGHT_GLOBS:
        for candidate in path.glob(pattern):
            if is_real_model_blob(candidate) and candidate not in seen:
                found.append(candidate)
                seen.add(candidate)
    return found


def huggingface_snapshot_ready(path: Path) -> bool:
    """True, если в каталоге есть настоящие веса, а не только токенизатор."""
    return bool(huggingface_weight_files(path))


def resolve_huggingface_source(root: Path, repo_id: str) -> str:
    """Локальный снимок, если готов; иначе исходный org/name для Hub."""
    name = (repo_id or '').strip()
    if not name:
        return repo_id or ''
    dest = huggingface_snapshot_dir(root, name)
    if huggingface_snapshot_ready(dest):
        return str(dest)
    return name


def cache_pip_dir(root: Path) -> Path:
    return cache_dir(root) / 'pip'


def cache_poetry_dir(root: Path) -> Path:
    return cache_dir(root) / 'poetry'


def cache_npm_dir(root: Path) -> Path:
    return cache_dir(root) / 'npm'


def cache_downloads_dir(root: Path) -> Path:
    """Кэш архивов portable Python / Node.js (и подобных runtime)."""
    return cache_dir(root) / 'downloads'


def npm_root_dir(root: Path) -> Path:
    """Каталог npm workspace (package.json, lock, node_modules)."""
    return virtual_env_dir(root) / 'npm'


def npm_node_modules_dir(root: Path) -> Path:
    return npm_root_dir(root) / 'node_modules'


def cache_tmp_dir(root: Path) -> Path:
    return cache_dir(root) / 'tmp'


def cache_system_test_dir(root: Path) -> Path:
    """Изолированные прогоны ergoms system-test."""
    return cache_tmp_dir(root) / 'system-test'


def cache_playwright_dir(root: Path) -> Path:
    """Кэш браузеров Playwright для системных e2e."""
    return cache_dir(root) / 'playwright'


def cache_loadtest_dir(root: Path) -> Path:
    """Артефакты loadtest (temp databases.yaml, ephemeral)."""
    return cache_dir(root) / 'loadtest'


def cache_docker_dir(root: Path) -> Path:
    """BuildKit local cache (DOCKER_DEPS_CACHE=project)."""
    return cache_dir(root) / 'docker-cache'


def cache_celery_balance_dir(root: Path) -> Path:
    """Overlay и история балансировщика Celery (не в git)."""
    return cache_dir(root) / 'celery_balance'


def env_secrets_lock_path(root: Path) -> Path:
    """Межпроцессный lock записи секретов в .env / databases.yaml."""
    return cache_dir(root) / 'env_secrets.lock'


def nssm_dir(root: Path) -> Path:
    return packages_dir(root) / 'nssm'


def portable_python_dir(root: Path) -> Path:
    """Portable CPython (python-build-standalone) — база для project venv."""
    return packages_dir(root) / 'python'


def portable_python_exe(root: Path) -> Path:
    base = portable_python_dir(root)
    if sys.platform == 'win32':
        return base / 'python.exe'
    return base / 'bin' / 'python3'


def nodejs_dir(root: Path) -> Path:
    return packages_dir(root) / 'nodejs'


def nodejs_exe(root: Path) -> Path:
    base = nodejs_dir(root)
    if sys.platform == 'win32':
        return base / 'node.exe'
    return base / 'bin' / 'node'


def npm_exe(root: Path) -> Path:
    base = nodejs_dir(root)
    if sys.platform == 'win32':
        cmd = base / 'npm.cmd'
        if cmd.is_file():
            return cmd
        return base / 'npm'
    return base / 'bin' / 'npm'


def nodejs_bin_dir(root: Path) -> Path:
    base = nodejs_dir(root)
    if sys.platform == 'win32':
        return base
    return base / 'bin'


def client_remotes_dir(root: Path) -> Path:
    """Собранные federated remotes: virtual_env/client-remotes/<name>/remoteEntry.js."""
    return virtual_env_dir(root) / 'client-remotes'


def jupyter_dir(root: Path) -> Path:
    return virtual_env_dir(root) / 'jupyter'


def jupyter_kernels_dir(root: Path) -> Path:
    return jupyter_dir(root) / 'kernels'


def letsencrypt_dir(root: Path) -> Path:
    return packages_dir(root) / 'letsencrypt'


def certbot_bin(root: Path) -> Path:
    return virtual_env_dir(root) / 'python' / 'bin' / 'certbot'


def certbot_webroot_dir(root: Path) -> Path:
    return packages_dir(root) / 'certbot' / 'webroot'


def meilisearch_runtime_dir(root: Path) -> Path:
    """Runtime Meilisearch (pid, dumps) в cache."""
    return cache_dir(root) / 'meilisearch'


def meilisearch_data_dir(root: Path) -> Path:
    """Индексы Meilisearch (LMDB) в cache."""
    return meilisearch_runtime_dir(root) / 'data.ms'


def wrappers_dir(root: Path) -> Path:
    return root / 'core' / 'deployment' / 'wrappers'


def systemd_units_dir(root: Path) -> Path:
    return wrappers_dir(root) / 'systemd'


def systemd_env_file(root: Path) -> Path:
    return wrappers_dir(root) / 'ergo_ms.env'


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def tool_cache_environ(root: Path) -> dict[str, str]:
    """Переменные кэша pip / Poetry / npm внутри virtual_env/cache."""
    pip = str(ensure_dir(cache_pip_dir(root)))
    poetry = str(ensure_dir(cache_poetry_dir(root)))
    npm = str(ensure_dir(cache_npm_dir(root)))
    return {
        'PIP_CACHE_DIR': pip,
        'POETRY_CACHE_DIR': poetry,
        'npm_config_cache': npm,
        'NPM_CONFIG_CACHE': npm,
    }
