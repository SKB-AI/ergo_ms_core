from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from db_backup_common import (  # noqa: E402
    BackupError,
    build_manifest,
    copy_sqlite,
    dump_filename,
    latest_snapshot_dir,
    list_snapshot_dirs,
    load_sql_sections,
    new_snapshot_dir,
    parse_backup_keep,
    parse_backup_schedule,
    prune_old_snapshots,
    resolve_pg_tool,
    resolve_snapshot_dir,
    should_use_docker_exec,
    snapshot_stamp,
    write_manifest,
)
from project_layout import backups_dir  # noqa: E402


def _write_yaml(root: Path, body: str) -> None:
    (root / 'databases.yaml').write_text(body, encoding='utf-8')


class DbBackupTests(unittest.TestCase):
    def test_snapshot_stamp_format(self) -> None:
        stamp = snapshot_stamp(datetime(2026, 9, 3, 12, 30, 45))
        self.assertEqual(stamp, '2026-09-03_123045')

    def test_new_snapshot_dir_under_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = new_snapshot_dir(root, '2026-09-03_123045')
            self.assertEqual(dest, backups_dir(root) / '2026-09-03_123045')
            self.assertTrue(dest.is_dir())

    def test_load_sql_sections_skips_redis_and_filters_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_yaml(
                root,
                'databases:\n'
                '  default:\n'
                '    engine: postgresql\n'
                '    name: ergo_ms\n'
                '    user: postgres\n'
                '    password: secret\n'
                '    host: 127.0.0.1\n'
                '    port: 5433\n'
                '  celery:\n'
                '    engine: postgresql\n'
                '    name: ergo_ms_celery\n'
                '    user: celery\n'
                '    password: secret\n'
                '    host: 127.0.0.1\n'
                '    port: 5433\n'
                '  redis:\n'
                '    engine: redis\n'
                '    host: 127.0.0.1\n'
                '    password: secret\n',
            )
            all_sections = load_sql_sections(root, ergo_db='portable_postgres')
            self.assertEqual([item['alias'] for item in all_sections], ['default', 'celery'])
            self.assertTrue(all(item['engine'] == 'postgresql' for item in all_sections))
            only_default = load_sql_sections(root, ergo_db='portable_postgres', only_alias='default')
            self.assertEqual([item['alias'] for item in only_default], ['default'])
            with self.assertRaises(BackupError):
                load_sql_sections(root, ergo_db='portable_postgres', only_alias='missing')

    def test_sqlite_copy_and_manifest_without_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'virtual_env' / 'resources' / 'db.sqlite3'
            src.parent.mkdir(parents=True)
            src.write_text('sqlite-bytes', encoding='utf-8')
            dest = root / 'virtual_env' / 'backups' / 'copy.sqlite3'
            copy_sqlite(src, dest)
            self.assertTrue(dest.is_file())
            self.assertGreater(dest.stat().st_size, 0)
            sections = [{
                'alias': 'default',
                'engine': 'sqlite',
                'name': str(src),
                'password': 'must-not-appear',
                'user': 'hidden',
                'host': '127.0.0.1',
            }]
            payload = build_manifest(
                created_at='2026-09-03T09:30:45+00:00',
                ergo_db='sqlite',
                ergo_runtime='host',
                sections=sections,
            )
            snapshot = new_snapshot_dir(root, '2026-09-03_123045')
            path = write_manifest(snapshot, payload)
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('must-not-appear', text)
            self.assertNotIn('hidden', text)
            data = json.loads(text)
            self.assertEqual(data['ergo_db'], 'sqlite')
            self.assertEqual(data['sections'][0]['filename'], dump_filename('default', 'sqlite'))
            self.assertEqual(data['sections'][0]['alias'], 'default')
            self.assertNotIn('password', data['sections'][0])
            self.assertNotIn('user', data['sections'][0])
            self.assertNotIn('host', data['sections'][0])

    def test_latest_and_resolve_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = new_snapshot_dir(root, '2026-09-01_100000')
            newer = new_snapshot_dir(root, '2026-09-03_123045')
            write_manifest(older, build_manifest(
                created_at='a', ergo_db='sqlite', ergo_runtime='host',
                sections=[{'alias': 'default', 'engine': 'sqlite', 'name': 'db'}],
            ))
            write_manifest(newer, build_manifest(
                created_at='b', ergo_db='sqlite', ergo_runtime='host',
                sections=[{'alias': 'default', 'engine': 'sqlite', 'name': 'db'}],
            ))
            self.assertEqual([path.name for path in list_snapshot_dirs(root)], [
                '2026-09-01_100000',
                '2026-09-03_123045',
            ])
            self.assertEqual(latest_snapshot_dir(root), newer)
            resolved = resolve_snapshot_dir(root, 'virtual_env/backups/2026-09-03_123045')
            self.assertEqual(resolved, newer)
            self.assertEqual(resolve_snapshot_dir(root, '2026-09-03_123045'), newer)
            with self.assertRaises(BackupError):
                resolve_snapshot_dir(root, 'virtual_env/backups/missing')

    def test_resolve_pg_tool_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('db_backup_common.shutil.which', return_value=None):
                with self.assertRaises(BackupError):
                    resolve_pg_tool(root, 'pg_dump')

    def test_docker_exec_only_for_compose_service_host(self) -> None:
        self.assertFalse(should_use_docker_exec('127.0.0.1', 'docker', 'postgres'))
        self.assertFalse(should_use_docker_exec('localhost', 'host', 'postgres'))
        self.assertTrue(should_use_docker_exec('postgres', 'docker', 'postgres'))
        self.assertFalse(should_use_docker_exec('postgres', 'host', 'postgres'))

    def test_parse_backup_keep_and_schedule(self) -> None:
        self.assertEqual(parse_backup_keep('7'), 7)
        self.assertEqual(parse_backup_keep('0'), 0)
        self.assertEqual(parse_backup_keep('-2'), 0)
        self.assertEqual(parse_backup_keep('nope', default=7), 7)
        self.assertEqual(parse_backup_keep(''), 7)
        self.assertIsNone(parse_backup_schedule('off'))
        self.assertIsNone(parse_backup_schedule(''))
        self.assertIsNone(parse_backup_schedule('false'))
        daily = parse_backup_schedule('03:00')
        self.assertIsNotNone(daily)
        self.assertEqual(daily.kind, 'daily')
        self.assertEqual((daily.hour, daily.minute), (3, 0))
        self.assertEqual(daily.cron_expr(), '0 3 * * *')
        self.assertEqual(parse_backup_schedule('3:15').minute, 15)
        self.assertEqual(parse_backup_schedule('22').hour, 22)
        self.assertEqual(parse_backup_schedule('true').label(), 'daily 03:00')
        self.assertEqual(parse_backup_schedule('daily').hour, 3)
        self.assertEqual(parse_backup_schedule('daily 22:30').label(), 'daily 22:30')
        interval = parse_backup_schedule('every 6h')
        self.assertEqual(interval.kind, 'interval')
        self.assertEqual(interval.every_hours, 6)
        self.assertEqual(interval.cron_expr(), '0 */6 * * *')
        self.assertEqual(interval.schtasks_args(), ['/SC', 'HOURLY', '/MO', '6'])
        self.assertEqual(parse_backup_schedule('12h').every_hours, 12)
        weekly = parse_backup_schedule('weekly sun 03:00')
        self.assertEqual(weekly.kind, 'weekly')
        self.assertEqual(weekly.weekday, 0)
        self.assertEqual(weekly.cron_expr(), '0 3 * * 0')
        self.assertEqual(weekly.schtasks_args()[3], 'SUN')
        self.assertIsNone(parse_backup_schedule('25:00'))
        self.assertIsNone(parse_backup_schedule('12:99'))
        self.assertIsNone(parse_backup_schedule('every 0h'))
        self.assertIsNone(parse_backup_schedule('weekly foo 03:00'))

    def test_prune_old_snapshots_keeps_newest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stamp in ('2026-09-01_100000', '2026-09-02_100000', '2026-09-03_100000'):
                dest = new_snapshot_dir(root, stamp)
                write_manifest(
                    dest,
                    build_manifest(
                        created_at=stamp,
                        ergo_db='sqlite',
                        ergo_runtime='host',
                        sections=[{'alias': 'default', 'engine': 'sqlite', 'name': 'db'}],
                    ),
                )
            removed = prune_old_snapshots(root, 2)
            self.assertEqual([path.name for path in removed], ['2026-09-01_100000'])
            self.assertEqual(
                [path.name for path in list_snapshot_dirs(root)],
                ['2026-09-02_100000', '2026-09-03_100000'],
            )
            self.assertEqual(prune_old_snapshots(root, 0), [])
            self.assertEqual(len(list_snapshot_dirs(root)), 2)


if __name__ == '__main__':
    unittest.main()
