"""Логика ergoms security-check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ergo_modes import effective_deploy_type, ergo_security, ergo_security_enforce, ergo_security_is_explicit
from env_resolvers import load_merged_env

from .catalog import CatalogError, load_security_catalog
from .checkers import run_control_check
from .levels import normalize_security_level
from .report import Finding, Report, print_finding


def build_security_report(
    project_root: Path,
    *,
    profile: str | None = None,
    enforce: str | None = None,
    values: Mapping[str, str] | None = None,
) -> Report:
    root = project_root.resolve()
    env_values = dict(values) if values is not None else load_merged_env(root)
    catalog = load_security_catalog()

    if profile:
        level = normalize_security_level(profile)
        level_source = f'--profile {level}'
    else:
        level = ergo_security(env_values)
        level_source = (
            f'ERGO_SECURITY={level}'
            if ergo_security_is_explicit(env_values)
            else 'по умолчанию standard'
        )

    enforce_mode = normalize_enforce(enforce or ergo_security_enforce(env_values))
    deploy = effective_deploy_type(env_values)

    report = Report(
        level=level,
        level_source=level_source,
        enforce=enforce_mode,
        deploy_type=deploy,
    )

    if level == 'open' and deploy == 'production':
        report.add(
            Finding(
                control_id='meta.open_in_production',
                title='Сочетание уровней',
                severity='error',
                message='ERGO_SECURITY=open запрещён при ERGO_ENV=production',
            )
        )

    report.add(
        Finding(
            control_id='meta.modules',
            title='Модули',
            severity='info',
            message='проверка modules/*/security.yaml — этап 4',
        )
    )

    context = {
        'values': env_values,
        'level': level,
        'deploy_type': deploy,
        'root': root,
    }
    for control in catalog.controls:
        report.add(run_control_check(control, catalog, context))

    return report


def normalize_enforce(raw: str | None) -> str:
    value = (raw or 'warn').strip().lower()
    return value if value in {'off', 'warn', 'raise'} else 'warn'


def run_security_check(
    project_root: Path,
    *,
    profile: str | None = None,
    enforce: str | None = None,
    as_json: bool = False,
    format_console=None,
    t=None,
) -> int:
    try:
        report = build_security_report(
            project_root,
            profile=profile,
            enforce=enforce,
        )
    except CatalogError as exc:
        if as_json:
            print(json.dumps({'error': str(exc)}, ensure_ascii=False, indent=2))
        elif format_console and t:
            print(format_console('error', t('security_check_catalog_error', detail=str(exc))))
        else:
            print(f'[ERROR] {exc}')
        return 2

    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return report.exit_code(enforce=report.enforce)

    if format_console and t:
        print(
            format_console(
                'info',
                t(
                    'security_check_header',
                    level=report.level,
                    source=report.level_source,
                    enforce=report.enforce,
                    deploy=report.deploy_type,
                ),
            )
        )
        for finding in report.findings:
            print_finding(finding, format_console=format_console)
        print(
            format_console(
                'info',
                t(
                    'security_check_summary',
                    errors=report.error_count,
                    warnings=report.warning_count,
                    skips=report.skip_count,
                ),
            )
        )
    else:
        print(
            f'Уровень: {report.level} ({report.level_source}), '
            f'enforce={report.enforce}, deploy={report.deploy_type}'
        )
        for finding in report.findings:
            print(f'[{finding.severity.upper()}] {finding.control_id}: {finding.message}')

    return report.exit_code(enforce=report.enforce)
