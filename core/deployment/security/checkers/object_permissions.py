"""AST-скан ObjectPermissionMixin и сырых ModelViewSet в core/api (С8)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from security.catalog import Control, SecurityCatalog
from security.report import Finding

_MIXIN_NAME = 'ObjectPermissionMixin'
_OPTIONAL = frozenset({None, False, 'false', 'no', 'optional', 'none'})
_SAFE_BASES = frozenset({
    'ObjectPermissionMixin',
    'BaseModelViewSet',
    'BaseReadOnlyModelViewSet',
    'BaseModelViewSetGlobalAdmin',
    'BaseReadOnlyModelViewSetGlobalAdmin',
    'BaseViewSetGlobalAdmin',
})
_VIEWSET_BASES = frozenset({
    'ModelViewSet',
    'ReadOnlyModelViewSet',
    'GenericViewSet',
})
_ADMIN_MARKERS = frozenset({
    'IsGlobalAdmin',
    'CanReadAuditLog',
    'BaseAPIViewGlobalAdminMixin',
})
_SKIP_CLASS_NAMES = frozenset({
    'BaseModelViewSet',
    'BaseReadOnlyModelViewSet',
    'BaseModelViewSetGlobalAdmin',
    'BaseReadOnlyModelViewSetGlobalAdmin',
    'BaseViewSet',
    'BaseGenericViewSet',
    'BaseViewSetGlobalAdmin',
})


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def _base_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _class_mentions(node: ast.ClassDef, markers: frozenset[str]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in markers:
            return True
        if isinstance(child, ast.Attribute) and child.attr in markers:
            return True
    return False


def find_object_permission_mixin(core_api_src: Path) -> Path | None:
    """Ищет class ObjectPermissionMixin в core/api/src/core (первый hit)."""
    root = core_api_src / 'core'
    if not root.is_dir():
        return None
    for path in root.rglob('*.py'):
        if '__pycache__' in path.parts:
            continue
        try:
            source = path.read_text(encoding='utf-8')
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == _MIXIN_NAME:
                return path
    return None


def find_raw_model_viewsets(core_api_src: Path) -> list[str]:
    """ViewSet ядра без object-mixin, BaseModelViewSet или admin-маркера."""
    root = core_api_src / 'core'
    if not root.is_dir():
        return []
    found: list[str] = []
    for path in root.rglob('*.py'):
        if '__pycache__' in path.parts:
            continue
        try:
            source = path.read_text(encoding='utf-8')
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name in _SKIP_CLASS_NAMES:
                continue
            bases = _base_names(node)
            if not (bases & _VIEWSET_BASES):
                continue
            if bases & _SAFE_BASES:
                continue
            if _class_mentions(node, _ADMIN_MARKERS):
                continue
            rel = path.as_posix()
            found.append(f'{node.name} ({path.name})')
            _ = rel
    return sorted(found)


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    level = context['level']
    requirement = control.requirement(level)
    root = Path(context['root'])
    core_api_src = root / 'core' / 'api' / 'src'

    mixin_path = find_object_permission_mixin(core_api_src)
    mixin_present = mixin_path is not None
    raw = find_raw_model_viewsets(core_api_src) if mixin_present else []

    if requirement in _OPTIONAL:
        if mixin_present:
            rel = mixin_path.relative_to(root).as_posix() if mixin_path else ''
            return Finding(
                control_id=control.id,
                title=control.title,
                severity='ok',
                message=f'на уровне {level} не обязательны; mixin найден ({rel})',
            )
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'на уровне {level} object-level permissions не обязательны',
        )

    if not mixin_present:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=(
                f'уровень {level} требует object-level permissions, '
                f'но класс {_MIXIN_NAME} не найден в core/api/src/core'
            ),
        )

    rel = mixin_path.relative_to(root).as_posix()
    if raw:
        names = ', '.join(raw[:8])
        extra = f' и ещё {len(raw) - 8}' if len(raw) > 8 else ''
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='warning',
            message=(
                f'миксин найден ({rel}), сырые ViewSet без object-scope: '
                f'{names}{extra}; уровень {level} требует {requirement}'
            ),
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message=f'миксин найден ({rel}), сырых ModelViewSet без object-scope нет',
    )
