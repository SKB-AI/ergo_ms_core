"""Проверка пароля PostgreSQL в databases.yaml (не шаблон admin)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ergo_modes import ergo_db
from security.catalog import Control, SecurityCatalog
from security.checkers.broker_redis import _parse_simple_yaml_section
from security.report import Finding

_POSTGRES_MODES = frozenset({'postgres', 'portable_postgres'})


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def load_default_db_password(root: Path) -> str | None:
    """Пароль секции default или None, если yaml нет."""
    path = root / 'databases.yaml'
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    section = _parse_simple_yaml_section(text, 'default')
    if not section:
        return None
    return (section.get('password') or '').strip()


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    root = Path(context['root'])
    requirement = str(control.requirement(level) or 'optional')

    if ergo_db(values) not in _POSTGRES_MODES:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='PostgreSQL не используется (ERGO_DB)',
        )

    if requirement == 'optional':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='на open шаблонный пароль PostgreSQL допускается',
        )

    password = load_default_db_password(root)
    if password is None:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='databases.yaml без секции default — не проверяется',
        )

    insecure = catalog.ref_strings('insecure_db_passwords')
    is_template = (not password) or password.lower() in {item.lower() for item in insecure}

    if not is_template:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message='пароль PostgreSQL в databases.yaml не из шаблона',
        )

    if requirement == 'recommended':
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='warning',
            message=(
                'databases.yaml default.password пуст или совпадает с шаблоном '
                f'(уровень {level} рекомендует свой пароль)'
            ),
        )

    return Finding(
        control_id=control.id,
        title=control.title,
        severity=_sev(control),
        message=(
            'databases.yaml default.password пуст или совпадает с шаблоном '
            f'(уровень {level} требует свой пароль)'
        ),
    )
