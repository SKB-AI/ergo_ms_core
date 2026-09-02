"""Тесты контролей пароля PostgreSQL, ключа поиска и bind LLM API."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from security.catalog import load_security_catalog
from security.checkers import _REGISTRY
from security.checkers.db_password import run as db_password_run
from security.checkers.llm_listen import run as llm_listen_run
from security.checkers.search_master_key import run as search_master_key_run


class PostgresPasswordCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('db.postgres_password')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'db_postgres_password')
        self.assertIn('db_postgres_password', _REGISTRY)

    def _run(self, *, level: str, values: dict[str, str], password: str | None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if password is not None:
                (root / 'databases.yaml').write_text(
                    'databases:\n'
                    '  default:\n'
                    '    engine: postgresql\n'
                    f'    password: "{password}"\n',
                    encoding='utf-8',
                )
            return db_password_run(
                self.control,
                self.catalog,
                {'values': values, 'level': level, 'root': root},
            )

    def test_skip_sqlite(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_DB': 'sqlite'},
            password='admin',
        )
        self.assertEqual(finding.severity, 'ok')
        self.assertIn('не используется', finding.message)

    def test_open_ok_with_admin(self) -> None:
        finding = self._run(
            level='open',
            values={'ERGO_DB': 'portable_postgres'},
            password='admin',
        )
        self.assertEqual(finding.severity, 'ok')

    def test_standard_warning_admin(self) -> None:
        finding = self._run(
            level='standard',
            values={'ERGO_DB': 'portable_postgres'},
            password='admin',
        )
        self.assertEqual(finding.severity, 'warning')
        self.assertNotIn('admin', finding.message)

    def test_hardened_error_empty(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_DB': 'postgres'},
            password='',
        )
        self.assertEqual(finding.severity, 'error')

    def test_hardened_ok_custom(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_DB': 'postgres'},
            password='unique-db-pass-not-template',
        )
        self.assertEqual(finding.severity, 'ok')


class SearchMasterKeyCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('search.master_key')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'search_master_key')
        self.assertIn('search_master_key', _REGISTRY)

    def _run(self, *, level: str, values: dict[str, str]):
        return search_master_key_run(
            self.control,
            self.catalog,
            {'values': values, 'level': level, 'root': Path('.')},
        )

    def test_skip_when_search_disabled(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_SEARCH_ENABLED': 'false', 'MEILI_MASTER_KEY': ''},
        )
        self.assertEqual(finding.severity, 'ok')

    def test_open_allows_template(self) -> None:
        finding = self._run(
            level='open',
            values={'ERGO_SEARCH_ENABLED': 'true', 'MEILI_MASTER_KEY': 'ergo_ms_dev_meili_key'},
        )
        self.assertEqual(finding.severity, 'ok')

    def test_standard_warning_template(self) -> None:
        secret = 'ergo_ms_dev_meili_key'
        finding = self._run(
            level='standard',
            values={'ERGO_SEARCH_ENABLED': 'true', 'MEILI_MASTER_KEY': secret},
        )
        self.assertEqual(finding.severity, 'warning')
        self.assertNotIn(secret, finding.message)

    def test_hardened_error_template(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_SEARCH_ENABLED': 'true', 'MEILI_MASTER_KEY': 'ergo_ms_dev_meili_key'},
        )
        self.assertEqual(finding.severity, 'error')

    def test_hardened_ok_custom(self) -> None:
        finding = self._run(
            level='hardened',
            values={'ERGO_SEARCH_ENABLED': 'true', 'MEILI_MASTER_KEY': 'unique-search-key-32'},
        )
        self.assertEqual(finding.severity, 'ok')


class LlmListenLoopbackCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('llm.listen_loopback')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'llm_listen_loopback')
        self.assertIn('llm_listen_loopback', _REGISTRY)

    def _run(self, *, level: str, host: str = ''):
        return llm_listen_run(
            self.control,
            self.catalog,
            {'values': {'OLLAMA_HOST': host}, 'level': level, 'root': Path('.')},
        )

    def test_unset_ok(self) -> None:
        finding = self._run(level='hardened', host='')
        self.assertEqual(finding.severity, 'ok')

    def test_loopback_ok(self) -> None:
        finding = self._run(level='hardened', host='127.0.0.1:11434')
        self.assertEqual(finding.severity, 'ok')

    def test_open_allows_all_interfaces(self) -> None:
        finding = self._run(level='open', host='0.0.0.0:11434')
        self.assertEqual(finding.severity, 'ok')

    def test_standard_warning_all_interfaces(self) -> None:
        finding = self._run(level='standard', host='0.0.0.0:11434')
        self.assertEqual(finding.severity, 'warning')
        self.assertIn('OLLAMA_HOST', finding.message)

    def test_hardened_error_lan(self) -> None:
        finding = self._run(level='hardened', host='10.0.0.5:11434')
        self.assertEqual(finding.severity, 'error')


if __name__ == '__main__':
    unittest.main()
