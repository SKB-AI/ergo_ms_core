"""Паритет Docker nginx с host HTTP (С6)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from security.catalog import Control, SecurityCatalog
from security.report import Finding

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[2]
_NGINX_DIR = _DEPLOYMENT_DIR / 'nginx'
for _path in (_DEPLOYMENT_DIR, _NGINX_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from render_common import (  # noqa: E402
    build_docker_core_proxy_locations,
    build_docker_http_preamble,
    render_docker_nginx_config,
)

_OPTIONAL = frozenset({None, 'optional', False, 'false'})


def _sev(control: Control) -> str:
    return 'error' if control.violation == 'error' else 'warning'


def _docker_nginx_enabled(values: dict[str, str]) -> bool:
    profile = (values.get('DOCKER_PROFILE_NGINX') or '').strip().lower()
    if profile in ('1', 'true', 'yes', 'on'):
        return True
    return (values.get('ERGO_PROXY') or '').strip().lower() == 'nginx'


def _parity_ok(rendered: str) -> list[str]:
    """Возвращает список отсутствующих маркеров паритета."""
    missing: list[str] = []
    checks = (
        ('server_tokens off', 'http_hardening'),
        ('limit_req_zone', 'rate_limit zones'),
        ('X-Frame-Options', 'security headers'),
        ('limit_req zone=ergo_api', 'api rate limit'),
        ('limit_req zone=ergo_upload', 'upload rate limit'),
        ('location /health/', 'health location'),
    )
    for needle, label in checks:
        if needle not in rendered:
            missing.append(label)
    health_idx = rendered.find('location /health/')
    if health_idx >= 0:
        chunk = rendered[health_idx:health_idx + 200]
        if 'deny all' not in chunk:
            missing.append('health ACL (deny)')
    return missing


def run(control: Control, catalog: SecurityCatalog, context: dict[str, Any]) -> Finding:
    values = context['values']
    level = context['level']
    requirement = control.requirement(level)

    if requirement in _OPTIONAL:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity='ok',
            message=f'на уровне {level} паритет Docker nginx не обязателен',
        )

    root = Path(context['root'])
    template = root / 'core' / 'deployment' / 'docker' / 'nginx' / 'ergo_ms.docker.conf.template'
    if not template.is_file():
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message='не найден ergo_ms.docker.conf.template',
        )

    try:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            out = Path(tmp) / 'docker.conf'
            render_docker_nginx_config(values, template_path=template, output_path=out)
            rendered = out.read_text(encoding='utf-8')
    except Exception as exc:  # noqa: BLE001
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message=f'не удалось отрендерить Docker nginx: {exc}',
        )

    if 'server_tokens' not in build_docker_http_preamble():
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message='Docker http preamble без server_tokens',
        )
    if 'deny all' not in build_docker_core_proxy_locations():
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message='Docker /health/ без deny all',
        )

    missing = _parity_ok(rendered)
    if missing:
        return Finding(
            control_id=control.id,
            title=control.title,
            severity=_sev(control),
            message='Docker nginx слабее host: ' + ', '.join(missing),
        )

    note = ''
    if not _docker_nginx_enabled(values):
        note = ' (профиль nginx сейчас выключен — шаблон в порядке)'
    return Finding(
        control_id=control.id,
        title=control.title,
        severity='ok',
        message='паритет с host HTTP: headers, rate limit, /health/ deny' + note,
    )
