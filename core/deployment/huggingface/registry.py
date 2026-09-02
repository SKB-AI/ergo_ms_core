"""
Discovery modules/*/huggingface_models.yaml.

Вне Django. Имена модулей не хардкодятся — только ModuleCatalog.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

_HF_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _HF_DIR.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from console_tags import format_console  # noqa: E402
from env_file_loader import load_project_env, parse_env_file  # noqa: E402
from lifecycle.modules.catalog import ModuleCatalog  # noqa: E402

from .models import ResolvedHfModel  # noqa: E402

HOOK_FILENAME = 'huggingface_models.yaml'


class HfRegistryError(Exception):
    """Ошибка загрузки реестра снимков Hugging Face."""


def _warn(message: str) -> None:
    print(format_console('warning', message), file=sys.stderr)


def _load_merged_env(root: Path) -> dict[str, str]:
    merged = load_project_env(root)
    modules_dir = root / 'modules'
    if not modules_dir.is_dir():
        return merged
    env_files = [
        path
        for path in modules_dir.rglob('.env')
        if path.is_file() and not path.name.endswith('.example')
    ]
    for env_file in sorted(env_files):
        merged.update(parse_env_file(env_file))
    return merged


def _resolve_repo_id(entry: dict[str, Any], *, env_values: dict[str, str]) -> str:
    explicit = str(entry.get('repo') or entry.get('name') or '').strip()
    if explicit:
        return explicit
    env_key = str(entry.get('env') or '').strip()
    if not env_key:
        return ''
    value = (env_values.get(env_key) or '').strip()
    if value:
        return value
    return str(entry.get('default') or '').strip()


def _parse_hook(
    path: Path,
    *,
    root: Path,
    module_dir_name: str,
    env_values: dict[str, str],
) -> list[ResolvedHfModel]:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        _warn(f'{path}: не удалось прочитать ({exc})')
        return []
    if not text.strip():
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _warn(f'{path}: ошибка YAML ({exc})')
        return []
    if not isinstance(data, dict):
        _warn(f'{path}: корень должен быть объектом')
        return []

    module = str(data.get('module') or module_dir_name).strip() or module_dir_name
    raw_models = data.get('models')
    if raw_models is None:
        return []
    if not isinstance(raw_models, list):
        _warn(f'{path}: models должен быть списком')
        return []

    resolved: list[ResolvedHfModel] = []
    try:
        relative = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        relative = path.as_posix()

    for index, item in enumerate(raw_models):
        if not isinstance(item, dict):
            _warn(f'{path}: models[{index}] должен быть объектом')
            continue
        repo_id = _resolve_repo_id(item, env_values=env_values)
        if not repo_id:
            _warn(f'{path}: models[{index}] — пустой repo (нужны repo/name или env+default)')
            continue
        required_raw = item.get('required', True)
        required = bool(required_raw) if required_raw is not None else True
        resolved.append(
            ResolvedHfModel(
                repo_id=repo_id,
                required=required,
                source_module=module,
                source_file=relative,
            )
        )
    return resolved


def _dedupe(models: list[ResolvedHfModel]) -> list[ResolvedHfModel]:
    by_id: dict[str, ResolvedHfModel] = {}
    for model in models:
        key = model.repo_id.strip().lower()
        existing = by_id.get(key)
        if existing is None:
            by_id[key] = model
            continue
        if model.required and not existing.required:
            by_id[key] = ResolvedHfModel(
                repo_id=model.repo_id,
                required=True,
                source_module=model.source_module,
                source_file=model.source_file,
            )
    return list(by_id.values())


def load_resolved_models(root: Path) -> tuple[ResolvedHfModel, ...]:
    catalog = ModuleCatalog.from_env(root)
    env_values = _load_merged_env(root)
    collected: list[ResolvedHfModel] = []
    for module_dir in catalog.iter_module_dirs():
        path = module_dir / HOOK_FILENAME
        if not path.is_file():
            continue
        collected.extend(
            _parse_hook(
                path,
                root=root,
                module_dir_name=module_dir.name,
                env_values=env_values,
            )
        )
    return tuple(_dedupe(collected))
