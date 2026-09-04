from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from install_infra_log_rotate import _linux_cron_line  # noqa: E402
from log_hygiene import (  # noqa: E402
    compress_numbered_backups,
    format_bytes,
    gzip_replace,
    list_log_files,
    prune_numbered_backups,
    shift_backups,
)
from rotate_infra_logs import (  # noqa: E402
    print_logs_status,
    rotate_copytruncate,
    rotate_rename,
)


class LogHygieneTests(unittest.TestCase):
    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(900), '900B')
        self.assertEqual(format_bytes(1024), '1.0K')
        self.assertEqual(format_bytes(10 * 1024 * 1024), '10.0M')

    def test_gzip_replace_shrinks_and_removes_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'ollama-serve.log.1'
            src.write_text('hello log line\n' * 4000, encoding='utf-8')
            before = src.stat().st_size
            dest, reported_before, after = gzip_replace(src)
            self.assertEqual(dest, Path(tmp) / 'ollama-serve.log.1.gz')
            self.assertEqual(reported_before, before)
            self.assertFalse(src.exists())
            self.assertTrue(dest.is_file())
            self.assertLess(after, before)

    def test_shift_backups_keeps_gz_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / 'ergoms.log'
            first = Path(tmp) / 'ergoms.log.1.gz'
            first.write_bytes(b'gzipped-one')
            shift_backups(live, 3)
            self.assertFalse(first.exists())
            self.assertTrue((Path(tmp) / 'ergoms.log.2.gz').is_file())

    def test_compress_numbered_backups_skips_already_gz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / 'api.log'
            raw = Path(tmp) / 'api.log.1'
            already = Path(tmp) / 'api.log.2.gz'
            raw.write_text('plain backup\n' * 200, encoding='utf-8')
            already.write_bytes(b'already')
            done = compress_numbered_backups(live, 5)
            self.assertEqual(len(done), 1)
            self.assertEqual(done[0][0], Path(tmp) / 'api.log.1.gz')
            self.assertFalse(raw.exists())
            self.assertTrue(already.is_file())

    def test_prune_removes_over_count_and_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / 'redis.log'
            extra = Path(tmp) / 'redis.log.4'
            extra.write_text('extra', encoding='utf-8')
            old = Path(tmp) / 'redis.log.1.gz'
            old.write_bytes(b'old')
            old_mtime = time.time() - 20 * 86400
            os.utime(old, (old_mtime, old_mtime))
            removed = prune_numbered_backups(live, backup_count=3, retention_days=14)
            names = {path.name for path in removed}
            self.assertIn('redis.log.4', names)
            self.assertIn('redis.log.1.gz', names)

    def test_rotate_copytruncate_then_gzip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ollama-serve.log'
            path.write_text('x' * 200, encoding='utf-8')
            self.assertTrue(rotate_copytruncate(path, max_bytes=50, backup_count=2))
            self.assertEqual(path.stat().st_size, 0)
            self.assertTrue((Path(tmp) / 'ollama-serve.log.1').is_file())
            compress_numbered_backups(path, 2)
            self.assertTrue((Path(tmp) / 'ollama-serve.log.1.gz').is_file())
            self.assertFalse((Path(tmp) / 'ollama-serve.log.1').exists())

    def test_rotate_rename_skips_small_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nginx-error.log'
            path.write_text('tiny', encoding='utf-8')
            self.assertFalse(rotate_rename(path, max_bytes=1000, backup_count=2))
            self.assertTrue(path.is_file())

    def test_status_json_lists_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / 'logs'
            logs.mkdir()
            (logs / 'ollama-serve.log').write_text('abc', encoding='utf-8')
            (logs / '.gitkeep').write_text('', encoding='utf-8')
            buf = io.StringIO()
            with patch('rotate_infra_logs.resolve_logs_dir', return_value=logs):
                with patch('sys.stdout', buf):
                    code = print_logs_status(root, as_json=True)
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload['total_bytes'], 3)
            self.assertEqual([item['path'] for item in payload['files']], ['ollama-serve.log'])

    def test_hourly_cron_uses_every_hour(self) -> None:
        root = Path('/projects/ergo_ms')
        hourly = _linux_cron_line(root, 3, 'hourly')
        daily = _linux_cron_line(root, 3, 'daily')
        self.assertTrue(hourly.startswith('0 * * * *'))
        self.assertTrue(daily.startswith('0 3 * * *'))
        self.assertIn('rotate_infra_logs.py', hourly)

    def test_list_log_files_sorts_by_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / 'small.log').write_text('a', encoding='utf-8')
            (logs / 'big.log').write_text('bbbbbb', encoding='utf-8')
            names = [item.path.name for item in list_log_files(logs)]
            self.assertEqual(names, ['big.log', 'small.log'])


if __name__ == '__main__':
    unittest.main()
