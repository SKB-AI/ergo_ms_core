"""
Загрузка корневого .env и фрагментов env/*.env (stdlib, без Django).

Порядок merge (позже перекрывает раньше):
1. {root}/.env
2. {root}/env/*.env — приоритетные имена ниже, затем остальные по имени
"""

from __future__ import annotations

from pathlib import Path

_FRAGMENT_PRIORITY = (
    'nginx.env',
    'docker.env',
    'jupyter.env',
    'smtp.env',
    'logging.env',
    'mcp.env',
    'media.env',
    'realtime.env',
    'cache.env',
    'celery.env',
    'postgres.env',
    'search.env',
)


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    result: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, _, raw = stripped.partition('=')
        result[key.strip()] = raw.strip().strip('"').strip("'")
    return result


def env_dir(root: Path) -> Path:
    return root / 'env'


def list_fragment_env_files(root: Path) -> list[Path]:
    directory = env_dir(root)
    if not directory.is_dir():
        return []

    by_name = {
        path.name: path
        for path in directory.iterdir()
        if path.is_file() and path.suffix == '.env' and not path.name.endswith('.example')
    }

    ordered: list[Path] = []
    for name in _FRAGMENT_PRIORITY:
        path = by_name.pop(name, None)
        if path is not None:
            ordered.append(path)
    for name in sorted(by_name):
        ordered.append(by_name[name])
    return ordered


def load_project_env(root: Path, *, include_fragments: bool = True) -> dict[str, str]:
    """Сливает корневой .env и фрагменты env/*.env."""
    merged = parse_env_file(root / '.env')
    if include_fragments:
        for fragment in list_fragment_env_files(root):
            merged.update(parse_env_file(fragment))
    return merged


def apply_project_env_to_environ(
    root: Path,
    environ: dict[str, str] | None = None,
    *,
    override_existing: bool = False,
) -> dict[str, str]:
    """
    Применяет load_project_env к mapping (по умолчанию os.environ).

    По умолчанию не затирает уже заданные в процессе переменные
    (удобно для Docker/CI, где ключи уже в окружении).
    """
    import os

    target = environ if environ is not None else os.environ
    loaded = load_project_env(root)
    for key, value in loaded.items():
        if not override_existing and key in target and str(target[key]).strip() != '':
            continue
        target[key] = value
    return loaded
