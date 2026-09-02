"""CI: границы БД модулей — межмодульные FK и сырой SQL к чужим таблицам."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

_SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPTS_DIR.parent.parent.parent
MODULES_DIR = PROJECT_ROOT / 'modules'

_FK_TYPES = frozenset({'ForeignKey', 'OneToOneField', 'ManyToManyField'})
_CORE_APPS = frozenset({
    'cms_adp',
    'auth',
    'contenttypes',
    'sessions',
    'admin',
    'core_notifications',
    'core_audit',
    'core_search',
    'core_integrations',
    'settings',
    'core_realtime',
    'core_client_monitor',
})


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ''


def _to_target(call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg == 'to' and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    if call.args:
        arg = call.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.Attribute):
            parts: list[str] = []
            cur: ast.AST = arg
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            return '.'.join(reversed(parts))
        if isinstance(arg, ast.Name):
            return arg.id
    return ''


def _iter_model_files(module_dir: Path) -> Iterable[Path]:
    api = module_dir / 'api'
    if not api.is_dir():
        return
    skip = {'__pycache__', 'migrations', '.git'}
    for path in api.rglob('*.py'):
        if any(part in skip for part in path.parts):
            continue
        yield path


def _schema_hook(path: Path) -> dict:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    data = yaml.safe_load(text) or {}
    return data if isinstance(data, dict) else {}


def _isolated_modules() -> set[str]:
    """Модули с schema.yaml, у которых isolated не выключен явно."""
    found: set[str] = set()
    if not MODULES_DIR.is_dir():
        return found
    for child in MODULES_DIR.iterdir():
        hook = child / 'api' / 'schema.yaml'
        if not (child.is_dir() and hook.is_file()):
            continue
        data = _schema_hook(hook)
        if bool(data.get('isolated', True)):
            found.add(child.name)
    return found


def find_cross_module_fk_violations() -> list[tuple[str, str]]:
    """
    Возвращает (rel_path, message) для FK на другое приложение модуля.

    Ошибки — только у модулей с ``isolated: true`` в api/schema.yaml.
    Остальные — вызывающий код решает, warning это или skip.
    """
    isolated = _isolated_modules()
    violations: list[tuple[str, str]] = []
    if not MODULES_DIR.is_dir():
        return violations
    for module_dir in sorted(MODULES_DIR.iterdir()):
        if not module_dir.is_dir() or module_dir.name.startswith('.'):
            continue
        name = module_dir.name
        enforce = name in isolated
        for path in _iter_model_files(module_dir):
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            except (OSError, SyntaxError):
                continue
            rel = str(path.relative_to(PROJECT_ROOT)).replace('\\', '/')
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _call_name(node) not in _FK_TYPES:
                    continue
                target = _to_target(node)
                if not target or '.' not in target:
                    continue
                app = target.split('.', 1)[0]
                if app in ('self', name) or app.startswith('settings'):
                    continue
                if app in _CORE_APPS or app == 'AUTH_USER_MODEL':
                    if enforce and app not in ('self',):
                        # изолированный модуль не должен FK даже на auth
                        if app in ('cms_adp', 'AUTH_USER_MODEL') or 'AUTH_USER' in target:
                            violations.append((
                                rel,
                                f'{rel}:{getattr(node, "lineno", 0)}: isolated FK to {target}',
                            ))
                    continue
                if app != name:
                    if enforce:
                        violations.append((
                            rel,
                            f'{rel}:{getattr(node, "lineno", 0)}: cross-module FK {target}',
                        ))
    return violations


def find_isolated_auth_fk_violations() -> list[tuple[str, str]]:
    """FK на AUTH_USER_MODEL у модулей с isolated: true."""
    isolated = _isolated_modules()
    violations: list[tuple[str, str]] = []
    for name in isolated:
        module_dir = MODULES_DIR / name
        for path in _iter_model_files(module_dir):
            try:
                source = path.read_text(encoding='utf-8')
                tree = ast.parse(source, filename=str(path))
            except (OSError, SyntaxError):
                continue
            rel = str(path.relative_to(PROJECT_ROOT)).replace('\\', '/')
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _call_name(node) not in _FK_TYPES:
                    continue
                target = _to_target(node)
                if 'AUTH_USER' in target or target.endswith('ErgoUser'):
                    violations.append((
                        rel,
                        f'{rel}:{getattr(node, "lineno", 0)}: isolated module FK to {target}',
                    ))
    return violations
