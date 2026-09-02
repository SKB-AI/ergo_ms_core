"""Режимы безопасности ERGO MS (этап 2: каталог, отчёт и merge unset-ключей).

Публичные имена доступны через ``from security import …`` (lazy), чтобы
``from security.csp_policy import …`` не тянул PyYAML на portable Python
до ``python-install`` (setup-full / scaffold).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    'Finding',
    'Report',
    'SecurityCatalog',
    'load_security_catalog',
    'merge_security_profile_defaults',
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    'Finding': ('.report', 'Finding'),
    'Report': ('.report', 'Report'),
    'SecurityCatalog': ('.catalog', 'SecurityCatalog'),
    'load_security_catalog': ('.catalog', 'load_security_catalog'),
    'merge_security_profile_defaults': (
        '.profile_defaults',
        'merge_security_profile_defaults',
    ),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    module_name, attr = target
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attr)
    globals()[name] = value
    return value
