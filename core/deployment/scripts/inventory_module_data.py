"""
Инвентаризация связей данных модулей (FK, SQL, меню).

Использование: ergoms data-inventory [--json]

Ядро не хардкодит имена модулей: сканирует ``modules/*/api``.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent
MODULES_DIR = PROJECT_ROOT / 'modules'

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402

_FK_TYPES = frozenset({'ForeignKey', 'OneToOneField', 'ManyToManyField'})
_SQL_HINTS = ('RawSQL', 'extra(', '.raw(', 'connection.cursor', 'ExecuteSQL')
_AUTH_HINTS = ('AUTH_USER_MODEL', 'settings.AUTH_USER_MODEL', 'get_user_model')


def _module_dirs() -> list[Path]:
    if not MODULES_DIR.is_dir():
        return []
    return sorted(
        p for p in MODULES_DIR.iterdir()
        if p.is_dir() and not p.name.startswith('.') and (p / 'api').is_dir()
    )


def _iter_py(root: Path) -> list[Path]:
    skip = {'__pycache__', 'migrations', '.git'}
    files: list[Path] = []
    for path in root.rglob('*.py'):
        if any(part in skip for part in path.parts):
            continue
        files.append(path)
    return files


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ''


def _kw_str(call: ast.Call, name: str) -> str:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return ''


def _first_arg_str(call: ast.Call) -> str:
    if not call.args:
        return ''
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


def _scan_file(path: Path, module_name: str) -> dict[str, Any]:
    rel = str(path.relative_to(PROJECT_ROOT)).replace('\\', '/')
    try:
        source = path.read_text(encoding='utf-8')
    except OSError as exc:
        return {'file': rel, 'error': str(exc)}
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return {'file': rel, 'error': f'SyntaxError:{exc.lineno}'}

    fks: list[dict[str, Any]] = []
    sql_hits: list[dict[str, Any]] = []
    auth_fk = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in _FK_TYPES:
            target = _kw_str(node, 'to') or _first_arg_str(node)
            constraint = True
            for kw in node.keywords:
                if kw.arg == 'db_constraint' and isinstance(kw.value, ast.Constant):
                    constraint = bool(kw.value.value)
            kind = 'auth' if any(h in target for h in ('AUTH_USER', 'ErgoUser', 'auth.User')) else 'other'
            if kind == 'auth':
                auth_fk = True
            cross = False
            if '.' in target and not target.startswith('settings'):
                app = target.split('.', 1)[0]
                if app and app != module_name and app not in ('self',):
                    cross = True
            fks.append({
                'file': rel,
                'line': getattr(node, 'lineno', 0),
                'type': _call_name(node),
                'to': target,
                'db_constraint': constraint,
                'kind': kind,
                'cross_module': cross,
            })
        if isinstance(node, ast.Call) and _call_name(node) in {'RawSQL', 'extra', 'raw'}:
            sql_hits.append({'file': rel, 'line': getattr(node, 'lineno', 0), 'hint': _call_name(node)})

    lower = source
    for hint in _SQL_HINTS:
        if hint in lower and hint not in {h['hint'] for h in sql_hits}:
            sql_hits.append({'file': rel, 'line': 0, 'hint': hint})

    menu_mig = 'MenuMigrationHelper' in source or 'add_menu' in path.name
    return {
        'file': rel,
        'fks': fks,
        'sql': sql_hits,
        'auth_fk': auth_fk,
        'menu_migration': menu_mig,
        'has_auth_hint': any(h in source for h in _AUTH_HINTS),
    }


def _integrations_yaml(module_dir: Path) -> dict[str, Any]:
    path = module_dir / 'integrations.yaml'
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {'_raw': True}
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    return data if isinstance(data, dict) else {}


def inventory_one(module_dir: Path) -> dict[str, Any]:
    name = module_dir.name
    api = module_dir / 'api'
    scans = [_scan_file(path, name) for path in _iter_py(api)]
    fks = [fk for item in scans for fk in item.get('fks') or []]
    sql = [hit for item in scans for hit in item.get('sql') or []]
    deps = _integrations_yaml(module_dir)
    schema_hook = (api / 'schema.yaml').is_file()
    manifest = (api / 'bridge_manifest.yaml').is_file()
    cross = [fk for fk in fks if fk.get('cross_module')]
    auth = [fk for fk in fks if fk.get('kind') == 'auth']
    blockers: list[str] = []
    if cross:
        blockers.append('cross_module_fk')
    if auth:
        blockers.append('auth_user_fk')
    if any(item.get('menu_migration') for item in scans):
        blockers.append('menu_data_migration')
    if sql:
        blockers.append('raw_sql')
    requires = deps.get('requires') or []
    if requires:
        blockers.append('requires_peer')
    return {
        'module': name,
        'requires': requires if isinstance(requires, list) else [],
        'extends': deps.get('extends') or [],
        'schema_hook': schema_hook,
        'bridge_manifest': manifest,
        'fk_count': len(fks),
        'cross_module_fk': cross,
        'auth_user_fk': auth,
        'sql': sql,
        'blockers': blockers,
        'pilot_score': _pilot_score(blockers, cross, requires),
    }


def _pilot_score(blockers: list[str], cross: list, requires: list) -> str:
    if cross or requires:
        return 'late'
    if 'auth_user_fk' in blockers and len(blockers) <= 2:
        return 'candidate'
    if not blockers:
        return 'ready'
    return 'candidate'


def build_inventory() -> dict[str, Any]:
    modules = [inventory_one(d) for d in _module_dirs()]
    return {
        'modules': modules,
        'summary': {
            'count': len(modules),
            'with_cross_fk': sum(1 for m in modules if m['cross_module_fk']),
            'candidates': [m['module'] for m in modules if m['pilot_score'] == 'candidate'],
            'late': [m['module'] for m in modules if m['pilot_score'] == 'late'],
        },
    }


def _print_text(data: dict[str, Any]) -> None:
    print(format_console('info', t('data_inventory_heading', count=data['summary']['count'])))
    for item in data['modules']:
        blockers = ', '.join(item['blockers']) or '—'
        print(
            f"  {item['module']}: score={item['pilot_score']} "
            f"fk={item['fk_count']} blockers={blockers}"
        )
    summary = data['summary']
    print(format_console('info', t(
        'data_inventory_summary',
        candidates=', '.join(summary['candidates']) or '—',
        late=', '.join(summary['late']) or '—',
    )))


def main() -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description='Inventory module data couplings')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    data = build_inventory()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_text(data)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
