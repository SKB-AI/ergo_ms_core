"""Контракт против шторма POST /api/cms/adp/logout/.

Клиент шлёт один POST на волну; nginx на избыток отвечает 204, не 429.
Проверка: ergoms core-rules-check и test_logout_storm_guards.
"""

from __future__ import annotations

from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPTS_DIR.parent.parent.parent

REQUIRED_MARKERS: tuple[tuple[Path, str], ...] = (
    (
        PROJECT_ROOT / 'core' / 'deployment' / 'nginx' / 'render_common.py',
        'error_page 429 =204 @logout_limited',
    ),
    (
        PROJECT_ROOT / 'core' / 'deployment' / 'nginx' / 'render_common.py',
        'location @logout_limited',
    ),
    (
        PROJECT_ROOT / 'core' / 'client' / 'src' / 'core' / 'cms' / 'js' / 'tokenRefresh.js',
        '__ERGO_MS_LOGOUT_GATE__',
    ),
    (
        PROJECT_ROOT / 'core' / 'client' / 'src' / 'core' / 'cms' / 'js' / 'tokenRefresh.js',
        'axios.create',
    ),
    (
        PROJECT_ROOT / 'core' / 'client' / 'src' / 'core' / 'cms' / 'js' / 'tokenRefresh.js',
        'localStorage',
    ),
    (
        PROJECT_ROOT / 'core' / 'client' / 'src' / 'js' / 'api' / 'manager.js',
        'isLogoutApiUrl',
    ),
)


def find_logout_storm_guard_violations(
    project_root: Path | None = None,
) -> list[tuple[str, str]]:
    """Возвращает (относительный путь, маркер), если маркер пропал."""
    root = project_root or PROJECT_ROOT
    missing: list[tuple[str, str]] = []
    for path, marker in REQUIRED_MARKERS:
        rel_path = path
        if project_root is not None:
            rel_path = root / path.relative_to(PROJECT_ROOT)
        rel = str(rel_path.relative_to(root))
        try:
            text = rel_path.read_text(encoding='utf-8')
        except OSError:
            missing.append((rel, marker))
            continue
        if marker not in text:
            missing.append((rel, marker))
    return missing
