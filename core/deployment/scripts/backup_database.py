"""
Снимок и восстановление SQL-баз из databases.yaml.

Вызов: ergoms db-backup | ergoms db-restore --latest | ergoms db-restore --from=virtual_env/backups/<метка>
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from db_backup_common import (  # noqa: E402
    BackupError,
    backup_keep_limit,
    build_manifest,
    dump_filename,
    dump_section,
    latest_snapshot_dir,
    load_sql_sections,
    new_snapshot_dir,
    prune_old_snapshots,
    read_manifest,
    resolve_snapshot_dir,
    restore_section,
    snapshot_stamp,
    write_manifest,
)
from deployment_env import get_ergo_db, read_env  # noqa: E402


def _log(level: str, message: str) -> None:
    stream = sys.stderr if level == 'error' else sys.stdout
    print(format_console(level, message), file=stream, flush=True)


def _runtime() -> str:
    return (read_env('ERGO_RUNTIME', 'host') or 'host').strip().lower() or 'host'


def _confirm(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {'y', 'yes', 'д', 'да'}


def run_backup(root: Path, *, only_alias: str | None) -> int:
    ergo_db = get_ergo_db()
    runtime = _runtime()
    try:
        sections = load_sql_sections(root, ergo_db=ergo_db, only_alias=only_alias)
    except BackupError as exc:
        _log('error', str(exc))
        return 1
    stamp = snapshot_stamp()
    snapshot = new_snapshot_dir(root, stamp)
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    ok_sections: list[dict[str, str]] = []
    _log('info', t('db_backup_start', stamp=stamp, mode=ergo_db))
    for section in sections:
        dest = snapshot / dump_filename(section['alias'], section['engine'])
        _log(
            'info',
            t(
                'db_backup_section',
                alias=section['alias'],
                engine=section['engine'],
                name=section.get('name') or dest.name,
            ),
        )
        try:
            dump_section(root, section, dest, runtime=runtime)
        except BackupError as exc:
            _log('error', str(exc))
            return 1
        size_mb = dest.stat().st_size / (1024 * 1024) if dest.is_file() else 0.0
        _log('ok', t('db_backup_section_ready', alias=section['alias'], size_mb=f'{size_mb:.1f}'))
        ok_sections.append(section)
    write_manifest(
        snapshot,
        build_manifest(
            created_at=created,
            ergo_db=ergo_db,
            ergo_runtime=runtime,
            sections=ok_sections,
        ),
    )
    _log('ok', t('db_backup_done', path=str(snapshot)))
    keep = backup_keep_limit()
    removed = prune_old_snapshots(root, keep)
    if keep > 0 and removed:
        _log('info', t('db_backup_pruned', count=len(removed), keep=keep))
    elif keep > 0:
        _log('info', t('db_backup_keep_info', keep=keep))
    return 0


def _select_restore_dir(root: Path, *, latest: bool, from_path: str) -> Path | None:
    if latest and from_path:
        _log('error', t('db_restore_from_and_latest'))
        return None
    if latest:
        found = latest_snapshot_dir(root)
        if found is None:
            _log('error', t('db_restore_no_snapshots'))
            return None
        return found
    if from_path:
        try:
            return resolve_snapshot_dir(root, from_path)
        except BackupError as exc:
            _log('error', str(exc))
            return None
    _log('error', t('db_restore_need_source'))
    return None


def run_restore(
    root: Path,
    *,
    latest: bool,
    from_path: str,
    yes: bool,
    only_alias: str | None,
) -> int:
    snapshot = _select_restore_dir(root, latest=latest, from_path=from_path)
    if snapshot is None:
        return 1
    try:
        manifest = read_manifest(snapshot)
    except BackupError as exc:
        _log('error', str(exc))
        return 1
    items = [item for item in manifest['sections'] if isinstance(item, dict)]
    if only_alias:
        items = [item for item in items if item.get('alias') == only_alias]
        if not items:
            _log('error', t('db_backup_alias_unknown', alias=only_alias))
            return 1
    aliases = ', '.join(str(item.get('alias') or '') for item in items)
    _log('info', t('db_restore_start', path=str(snapshot), aliases=aliases))
    if not yes:
        if not _confirm(t('db_restore_confirm') + ' '):
            _log('warning', t('db_restore_cancelled'))
            return 1
    ergo_db = get_ergo_db()
    runtime = _runtime()
    try:
        live = {item['alias']: item for item in load_sql_sections(root, ergo_db=ergo_db)}
    except BackupError as exc:
        _log('error', str(exc))
        return 1
    for item in items:
        alias = str(item.get('alias') or '')
        filename = str(item.get('filename') or dump_filename(alias, str(item.get('engine') or '')))
        source = snapshot / filename
        section = live.get(alias)
        if section is None:
            _log('error', t('db_backup_alias_unknown', alias=alias))
            return 1
        _log('info', t('db_restore_section', alias=alias, engine=section['engine']))
        try:
            restore_section(root, section, source, runtime=runtime)
        except BackupError as exc:
            _log('error', str(exc))
            return 1
        _log('ok', t('db_restore_section_ready', alias=alias))
    _log('ok', t('db_restore_done', path=str(snapshot)))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=t('db_backup_cli_description'))
    parser.add_argument(
        'action',
        nargs='?',
        choices=('backup', 'restore'),
        default='backup',
        help=t('db_backup_cli_action'),
    )
    parser.add_argument('--database', dest='database', default='', help=t('db_backup_cli_database'))
    parser.add_argument('--from', dest='from_path', default='', help=t('db_restore_cli_from'))
    parser.add_argument('--latest', action='store_true', help=t('db_restore_cli_latest'))
    parser.add_argument('--yes', action='store_true', help=t('db_restore_cli_yes'))
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    args = _build_parser().parse_args(argv)
    alias = (args.database or '').strip() or None
    if args.action == 'restore':
        return run_restore(
            PROJECT_ROOT,
            latest=bool(args.latest),
            from_path=args.from_path,
            yes=bool(args.yes),
            only_alias=alias,
        )
    return run_backup(PROJECT_ROOT, only_alias=alias)


if __name__ == '__main__':
    raise SystemExit(main())
