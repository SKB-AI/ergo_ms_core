"""Логика ergoms security-modes."""

from __future__ import annotations

import json
from pathlib import Path

from ergo_modes import effective_deploy_type, ergo_security, ergo_security_enforce, ergo_security_is_explicit
from env_resolvers import load_merged_env

from .catalog import CatalogError, load_security_catalog
from .levels import LEVEL_ORDER, normalize_security_level, security_level_rank


def run_security_modes(
    project_root: Path,
    *,
    profile: str | None = None,
    show_controls: bool = False,
    as_json: bool = False,
    format_console=None,
    t=None,
) -> int:
    root = project_root.resolve()
    try:
        catalog = load_security_catalog()
        values = load_merged_env(root)
    except CatalogError as exc:
        if as_json:
            print(json.dumps({'error': str(exc)}, ensure_ascii=False, indent=2))
        elif format_console and t:
            print(format_console('error', t('security_modes_catalog_error', detail=str(exc))))
        else:
            print(f'[ERROR] {exc}')
        return 2

    effective = ergo_security(values)
    explicit = ergo_security_is_explicit(values)
    enforce = ergo_security_enforce(values)
    deploy = effective_deploy_type(values)
    focus = normalize_security_level(profile) if profile else effective

    payload = {
        'effective_level': effective,
        'level_source': f'ERGO_SECURITY={effective}' if explicit else 'default:standard',
        'enforce': enforce,
        'deploy_type': deploy,
        'focus_level': focus,
        'levels': [
            {
                'id': name,
                'rank': catalog.levels.get(name, security_level_rank(name)),
                'title': catalog.level_titles.get(name, ''),
            }
            for name in LEVEL_ORDER
        ],
        'open_in_production_forbidden': effective == 'open' and deploy == 'production',
        'stage': 2,
        'runtime_effect': True,
    }

    if show_controls:
        payload['controls'] = [
            {
                'id': c.id,
                'title': c.title,
                'kind': c.kind,
                'status': c.status,
                'check': c.check,
                'requirement': c.requirement(focus),
            }
            for c in catalog.controls
        ]

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    if format_console and t:
        print(
            format_console(
                'info',
                t(
                    'security_modes_header',
                    level=effective,
                    source=payload['level_source'],
                    enforce=enforce,
                    deploy=deploy,
                ),
            )
        )
        print(format_console('info', t('security_modes_stage2_note')))
        if payload['open_in_production_forbidden']:
            print(format_console('error', t('security_modes_open_in_production')))
        print(format_console('info', t('security_modes_levels_title')))
        for item in payload['levels']:
            mark = ' *' if item['id'] == effective else ''
            print(
                format_console(
                    'ok' if item['id'] == focus else 'info',
                    f"{item['id']} (rank {item['rank']}): {item['title']}{mark}",
                )
            )
        if show_controls:
            print(format_console('info', t('security_modes_controls_title', level=focus)))
            for c in payload['controls']:
                print(
                    format_console(
                        'info',
                        f"{c['id']} [{c['kind']}/{c['status']}] → {c['requirement']}",
                    )
                )
        return 0

    print(f"level={effective} source={payload['level_source']} enforce={enforce}")
    return 0
