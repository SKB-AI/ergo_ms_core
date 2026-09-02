"""AST-скан анонимных view ядра vs core_anonymous_allowlist.yaml."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from security.catalog import Control, SecurityCatalog
from security.levels import security_level_rank
from security.report import Finding

_PACKAGE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST_PATH = _PACKAGE_DIR / 'core_anonymous_allowlist.yaml'

_SKIP_CLASS_NAMES = frozenset({
    'BaseAPIView',
    'BaseAPIViewAuthMixin',
    'BaseAPIViewGlobalAdminMixin',
    'BaseAPIViewPublicMixin',
})

_AUTH_MIXINS = frozenset({
    'BaseAPIViewAuthMixin',
    'BaseAPIViewGlobalAdminMixin',
})

_ANON_BASES = frozenset({
    'BaseAPIViewPublicMixin',
    'TokenRefreshView',
})


@dataclass(frozen=True)
class AllowlistEntry:
    name: str
    actions: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    maximum_ok: bool = False
    note: str = ''


@dataclass
class Allowlist:
    entries: dict[str, AllowlistEntry] = field(default_factory=dict)
    path: Path | None = None

    def names(self) -> set[str]:
        return set(self.entries)


@dataclass(frozen=True)
class FoundAnonymous:
    name: str
    relative: str
    reason: str


def load_anonymous_allowlist(path: Path | None = None) -> Allowlist:
    allowlist_path = path or DEFAULT_ALLOWLIST_PATH
    raw = yaml.safe_load(allowlist_path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('allowlist root must be mapping')
    entries: dict[str, AllowlistEntry] = {}
    for item in raw.get('view_classes') or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        actions = tuple(str(a) for a in (item.get('actions') or []))
        prefixes = tuple(str(p) for p in (item.get('path_prefixes') or []))
        entries[name] = AllowlistEntry(
            name=name,
            actions=actions,
            path_prefixes=prefixes,
            maximum_ok=bool(item.get('maximum_ok', False)),
            note=str(item.get('note') or ''),
        )
    return Allowlist(entries=entries, path=allowlist_path)


def _base_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _permission_is_anonymous(node: ast.ClassDef) -> str | None:
    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name) or target.id != 'permission_classes':
                continue
            value = item.value
            if isinstance(value, (ast.List, ast.Tuple)):
                if len(value.elts) == 0:
                    return 'permission_classes=[]'
                for elt in value.elts:
                    if isinstance(elt, ast.Name) and elt.id == 'AllowAny':
                        return 'permission_classes=[AllowAny]'
                    if isinstance(elt, ast.Attribute) and elt.attr == 'AllowAny':
                        return 'permission_classes=[AllowAny]'
            if isinstance(value, ast.Name) and value.id in {'AllowAny', }:
                return 'permission_classes=AllowAny'
    return None


def _get_permissions_uses_allow_any(node: ast.ClassDef) -> bool:
    for item in node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name != 'get_permissions':
            continue
        for sub in ast.walk(item):
            if isinstance(sub, ast.Name) and sub.id == 'AllowAny':
                return True
            if isinstance(sub, ast.Attribute) and sub.attr == 'AllowAny':
                return True
    return False


def _overrides_authenticated(node: ast.ClassDef) -> bool:
    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name) or target.id != 'permission_classes':
                continue
            for sub in ast.walk(item.value):
                if isinstance(sub, ast.Name) and sub.id in {
                    'IsAuthenticated',
                    'IsGlobalAdmin',
                    'IsAdminUser',
                }:
                    return True
                if isinstance(sub, ast.Attribute) and sub.attr in {
                    'IsAuthenticated',
                    'IsGlobalAdmin',
                    'IsAdminUser',
                }:
                    return True
    return False


def classify_anonymous_class(node: ast.ClassDef) -> str | None:
    if node.name in _SKIP_CLASS_NAMES:
        return None

    bases = _base_names(node)
    if bases & _AUTH_MIXINS:
        # Auth mixin, но action-level AllowAny всё ещё возможен
        if _get_permissions_uses_allow_any(node):
            return 'get_permissions→AllowAny'
        return None

    perm = _permission_is_anonymous(node)
    if perm:
        return perm

    if _get_permissions_uses_allow_any(node):
        return 'get_permissions→AllowAny'

    if bases & _ANON_BASES:
        if _overrides_authenticated(node):
            return None
        if 'BaseAPIViewPublicMixin' in bases:
            return 'inherits BaseAPIViewPublicMixin'
        if 'TokenRefreshView' in bases:
            return 'inherits TokenRefreshView'

    # APIView с пустыми permission_classes уже пойман выше
    return None


def scan_core_anonymous_views(core_root: Path) -> list[FoundAnonymous]:
    found: list[FoundAnonymous] = []
    if not core_root.is_dir():
        return found
    for path in sorted(core_root.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(core_root).as_posix()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            reason = classify_anonymous_class(node)
            if reason:
                found.append(FoundAnonymous(name=node.name, relative=rel, reason=reason))
    return found


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values_level = context['level']
    requirement = str(control.requirement(values_level) or 'unrestricted')

    if requirement == 'unrestricted' or security_level_rank(values_level) <= 0:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='на open анонимные эндпоинты не ограничиваются',
        )

    root = Path(context['root'])
    core_api = context.get('core_api_root')
    if core_api is None:
        core_api = root / 'core' / 'api' / 'src' / 'core'
    else:
        core_api = Path(core_api)

    allowlist_path = context.get('anonymous_allowlist_path')
    try:
        allowlist = load_anonymous_allowlist(
            Path(allowlist_path) if allowlist_path else None,
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='skip',
            message=f'не удалось прочитать allowlist: {exc}',
        )

    found = scan_core_anonymous_views(core_api)
    allowed = allowlist.names()
    extras = [f for f in found if f.name not in allowed]

    if requirement == 'login_and_health_only':
        blocked = [
            f for f in found
            if f.name not in allowed or not allowlist.entries[f.name].maximum_ok
        ]
        if blocked:
            names = ', '.join(sorted({f.name for f in blocked}))
            return Finding(
                control_id=control.id,
                title=control.title,
                severity=_sev(control),
                message=f'вне login/health на maximum: {names}',
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'только login/health ({len(found)} точек)',
        )

    # standard / hardened (+ waivers этап 4)
    if extras:
        names = ', '.join(sorted({f.name for f in extras}))
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'вне allowlist ядра: {names}',
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'{len(found)} анонимных точек ядра в allowlist',
    )
