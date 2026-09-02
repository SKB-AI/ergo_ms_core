"""Загрузка и валидация profiles.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .levels import LEVEL_ORDER, normalize_security_level

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILES_PATH = _PACKAGE_DIR / 'profiles.yaml'

_REQUIRED_CONTROL_FIELDS = (
    'id',
    'layer',
    'title',
    'kind',
    'profiles',
    'violation',
    'waivable',
    'status',
    'check',
)


@dataclass(frozen=True)
class Control:
    id: str
    layer: str
    title: str
    kind: str
    profiles: dict[str, Any]
    violation: str
    waivable: bool
    status: str
    check: str
    env_key: str | None = None
    compare: str | None = None
    audit: tuple[str, ...] = ()

    def requirement(self, level: str) -> Any:
        return self.profiles.get(normalize_security_level(level))


@dataclass
class SecurityCatalog:
    version: int
    levels: dict[str, int]
    level_titles: dict[str, str]
    refs: dict[str, Any]
    controls: list[Control] = field(default_factory=list)
    path: Path | None = None

    def control_by_id(self, control_id: str) -> Control | None:
        for control in self.controls:
            if control.id == control_id:
                return control
        return None

    def insecure_secret_values(self) -> frozenset[str]:
        return self.ref_strings('insecure_secret_values')

    def ref_strings(self, key: str) -> frozenset[str]:
        raw = self.refs.get(key) or []
        if not isinstance(raw, list):
            return frozenset()
        return frozenset(str(item).strip() for item in raw if str(item).strip())


class CatalogError(ValueError):
    """Битый или неполный каталог контролей."""


def load_security_catalog(path: Path | None = None) -> SecurityCatalog:
    catalog_path = path or DEFAULT_PROFILES_PATH
    if not catalog_path.is_file():
        raise CatalogError(f'Каталог не найден: {catalog_path}')

    try:
        raw = yaml.safe_load(catalog_path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise CatalogError(f'Некорректный YAML: {exc}') from exc

    if not isinstance(raw, dict):
        raise CatalogError('Корень profiles.yaml должен быть mapping')

    version = int(raw.get('version') or 1)
    levels = raw.get('levels') or {}
    if not isinstance(levels, dict):
        raise CatalogError('levels должен быть mapping')
    for name in LEVEL_ORDER:
        if name not in levels:
            raise CatalogError(f'В levels отсутствует уровень {name}')

    level_titles = raw.get('level_titles') or {}
    if not isinstance(level_titles, dict):
        level_titles = {}

    refs = raw.get('refs') or {}
    if not isinstance(refs, dict):
        refs = {}

    controls_raw = raw.get('controls')
    if not isinstance(controls_raw, list) or not controls_raw:
        raise CatalogError('controls должен быть непустым списком')

    controls: list[Control] = []
    seen: set[str] = set()
    for item in controls_raw:
        if not isinstance(item, dict):
            raise CatalogError('Элемент controls должен быть mapping')
        for key in _REQUIRED_CONTROL_FIELDS:
            if key not in item:
                raise CatalogError(f'У контроля отсутствует поле {key}: {item.get("id")}')
        control_id = str(item['id']).strip()
        if not control_id:
            raise CatalogError('Пустой id контроля')
        if control_id in seen:
            raise CatalogError(f'Дубликат id контроля: {control_id}')
        seen.add(control_id)

        profiles = item['profiles']
        if not isinstance(profiles, dict):
            raise CatalogError(f'{control_id}: profiles должен быть mapping')
        for name in LEVEL_ORDER:
            if name not in profiles:
                raise CatalogError(f'{control_id}: нет требования для уровня {name}')

        kind = str(item['kind']).strip()
        if kind not in {'value', 'switch', 'policy'}:
            raise CatalogError(f'{control_id}: неизвестный kind={kind}')

        violation = str(item['violation']).strip()
        if violation not in {'error', 'warning'}:
            raise CatalogError(f'{control_id}: violation должен быть error|warning')

        status = str(item['status']).strip()
        if status not in {'implemented', 'partial', 'planned'}:
            raise CatalogError(f'{control_id}: status должен быть implemented|partial|planned')

        audit_raw = item.get('audit') or []
        if not isinstance(audit_raw, list):
            audit_raw = [audit_raw]

        controls.append(
            Control(
                id=control_id,
                layer=str(item['layer']).strip(),
                title=str(item['title']).strip(),
                kind=kind,
                profiles=dict(profiles),
                violation=violation,
                waivable=bool(item['waivable']),
                status=status,
                check=str(item['check']).strip(),
                env_key=(str(item['env_key']).strip() if item.get('env_key') else None),
                compare=(str(item['compare']).strip() if item.get('compare') else None),
                audit=tuple(str(a) for a in audit_raw),
            )
        )

    return SecurityCatalog(
        version=version,
        levels={str(k): int(v) for k, v in levels.items()},
        level_titles={str(k): str(v) for k, v in level_titles.items()},
        refs=refs,
        controls=controls,
        path=catalog_path,
    )
