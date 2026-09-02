"""Скачивание снимка org/name в virtual_env/trained_models/huggingface/."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_HF_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _HF_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from console_tags import format_console  # noqa: E402
from project_layout import (  # noqa: E402
    ensure_dir,
    huggingface_hub_cache_dir,
    huggingface_snapshot_dir,
    huggingface_snapshot_ready,
    is_real_model_blob,
    virtual_env_dir,
)

_IGNORE_PATTERNS = (
    '*.onnx',
    'onnx/**',
    'tf_model.h5',
    'tf_model*.h5',
    'flax_model*',
    'rust_model.ot',
    '*.ot',
    'openvino/**',
)
_STRIP_NAMES = (
    '*.onnx',
    'tf_model.h5',
    'flax_model.msgpack',
    'rust_model.ot',
)


def apply_hf_home(root: Path) -> None:
    """Кэш Hub только внутри корня проекта."""
    hub_home = ensure_dir(huggingface_hub_cache_dir(root))
    hub_blobs = ensure_dir(hub_home / 'hub')
    os.environ['HF_HOME'] = str(hub_home)
    os.environ['HF_HUB_CACHE'] = str(hub_blobs)
    os.environ.setdefault('HUGGINGFACE_HUB_CACHE', str(hub_blobs))
    # Xet через локальный прокси часто зависает на больших safetensors.
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')


def is_installed(root: Path, repo_id: str) -> bool:
    return huggingface_snapshot_ready(huggingface_snapshot_dir(root, repo_id))


def _legacy_snapshot_dir(root: Path, repo_id: str) -> Path | None:
    parts = [
        part
        for part in (repo_id or '').strip().strip('/').split('/')
        if part and part not in ('.', '..')
    ]
    if not parts:
        return None
    legacy = virtual_env_dir(root) / 'trained_models' / parts[-1]
    dest = huggingface_snapshot_dir(root, repo_id)
    try:
        if legacy.resolve() == dest.resolve():
            return None
    except OSError:
        return None
    return legacy


def adopt_legacy_snapshot(root: Path, repo_id: str) -> bool:
    """Перенести снимок из trained_models/<name> в huggingface/<org>/<name>."""
    dest = huggingface_snapshot_dir(root, repo_id)
    if huggingface_snapshot_ready(dest):
        return False
    legacy = _legacy_snapshot_dir(root, repo_id)
    if legacy is None or not huggingface_snapshot_ready(legacy):
        return False
    ensure_dir(dest)
    for item in legacy.iterdir():
        target = dest / item.name
        if target.exists():
            continue
        shutil.move(str(item), str(target))
    shutil.rmtree(legacy, ignore_errors=True)
    return huggingface_snapshot_ready(dest)


def strip_duplicate_formats(dest: Path) -> None:
    """Убрать flax/tf/onnx и черновик качалки; pytorch — если есть safetensors."""
    cache = dest / '.cache'
    if cache.is_dir():
        shutil.rmtree(cache, ignore_errors=True)
    for pattern in _STRIP_NAMES:
        for path in dest.glob(pattern):
            path.unlink(missing_ok=True)
    has_safetensors = any(
        is_real_model_blob(path) for path in dest.glob('*.safetensors')
    )
    if has_safetensors:
        pytorch = dest / 'pytorch_model.bin'
        if pytorch.is_file():
            pytorch.unlink(missing_ok=True)


def install(root: Path, repo_id: str, *, force: bool = False) -> int:
    root = root.resolve()
    dest = huggingface_snapshot_dir(root, repo_id)
    if not force:
        adopted = adopt_legacy_snapshot(root, repo_id)
        if is_installed(root, repo_id):
            strip_duplicate_formats(dest)
            if adopted:
                print(format_console('ok', f'{repo_id} перенесён в {dest}'))
            else:
                print(format_console('skip', f'{repo_id} уже установлен: {dest}'))
            return 0

    apply_hf_home(root)
    ensure_dir(dest)
    print(format_console('info', f'Скачиваю {repo_id} с Hugging Face → {dest}'))
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        print(
            format_console(
                'error',
                'Пакет huggingface_hub не установлен (ergoms python-install)',
            ),
            file=sys.stderr,
        )
        print(format_console('error', str(exc)), file=sys.stderr)
        return 1

    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(dest),
            cache_dir=os.environ['HF_HUB_CACHE'],
            ignore_patterns=list(_IGNORE_PATTERNS),
        )
    except Exception as exc:
        print(format_console('error', f'Не удалось скачать {repo_id}: {exc}'))
        return 1

    strip_duplicate_formats(dest)
    if not huggingface_snapshot_ready(dest):
        print(format_console('error', f'После загрузки нет весов в {dest}'))
        return 1

    print(format_console('ok', f'{repo_id} готов: {dest}'))
    return 0


def ensure_installed(root: Path, repo_id: str) -> Path:
    """Локальный каталог снимка в trained_models; при отсутствии — качает его туда."""
    root = root.resolve()
    name = (repo_id or '').strip()
    dest = huggingface_snapshot_dir(root, name)
    if not name:
        raise ValueError('repo_id Hugging Face пустой')
    if install(root, name) != 0 or not is_installed(root, name):
        raise RuntimeError(f'Не удалось установить снимок Hugging Face: {name}')
    return dest
