#!/usr/bin/env python3
"""
Отключает автооткрытие встроенного браузера Cursor.

Cursor не читает workbench.browser.openLocalhostLinks для своей вкладки
Browser Tab. Переключатель «Open Local Links in Cursor Browser» лежит
во внутреннем хранилище (state.vscdb), ключ cursor/autoOpenLocalhostUrls.

Вызывается из ergoms install-extensions и расширения ERGO MS User Config.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402

# Значения как пишет сам Cursor: строки "true" / "false".
CURSOR_BROWSER_KEYS = {
    'cursor/autoOpenLocalhostUrls': 'false',
}

# Раньше сюда ошибочно писали false: это выключало открытие https-ссылок
# в системном браузере (чат, агент, markdown). Ключ снимаем, чтобы Cursor
# вернулся к своему значению по умолчанию.
CURSOR_BROWSER_KEYS_DROP = (
    'cursor/glassOpenWebLinksInBrowser',
)


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except (AttributeError, OSError, ValueError):
                pass


def cursor_desktop_user_dir() -> Path | None:
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA', '').strip()
        if not appdata:
            return None
        return Path(appdata) / 'Cursor' / 'User'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Cursor' / 'User'
    return Path.home() / '.config' / 'Cursor' / 'User'


def cursor_remote_user_dir() -> Path | None:
    path = Path.home() / '.cursor-server' / 'data' / 'User'
    return path if path.is_dir() else None


def cursor_user_dir() -> Path | None:
    desktop = cursor_desktop_user_dir()
    if desktop is not None and desktop.is_dir():
        return desktop
    remote = cursor_remote_user_dir()
    if remote is not None:
        return remote
    return None


def cursor_state_db(user_dir: Path) -> Path:
    return user_dir / 'globalStorage' / 'state.vscdb'


def apply_browser_policy(db_path: Path) -> dict[str, object]:
    changed: list[str] = []
    unchanged: list[str] = []

    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        con.execute('PRAGMA busy_timeout = 10000')
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ItemTable'",
        )
        if cur.fetchone() is None:
            raise sqlite3.OperationalError('ItemTable missing')

        for key, value in CURSOR_BROWSER_KEYS.items():
            cur.execute('SELECT value FROM ItemTable WHERE key = ?', (key,))
            row = cur.fetchone()
            current = None if row is None else str(row[0])
            if current == value:
                unchanged.append(key)
                continue
            cur.execute(
                'INSERT INTO ItemTable (key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (key, value),
            )
            changed.append(key)

        for key in CURSOR_BROWSER_KEYS_DROP:
            cur.execute('SELECT value FROM ItemTable WHERE key = ?', (key,))
            if cur.fetchone() is None:
                continue
            cur.execute('DELETE FROM ItemTable WHERE key = ?', (key,))
            changed.append(key)
        con.commit()
    finally:
        con.close()

    return {
        'ok': True,
        'db': str(db_path),
        'changed': changed,
        'unchanged': unchanged,
    }


def _print_result(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return

    if payload.get('skipped'):
        print(format_console('skip', str(payload.get('reason') or '')))
        return

    changed = payload.get('changed') or []
    if changed:
        print(format_console(
            'ok',
            t('cursor_browser_policy_applied', count=len(changed)),
        ))
        print(format_console('info', t('cursor_browser_policy_reload')))
        return

    print(format_console('ok', t('cursor_browser_policy_already')))


def main() -> int:
    _configure_stdio_utf8()
    parser = argparse.ArgumentParser(
        description='Disable Cursor built-in browser auto-open',
    )
    parser.add_argument('--json', action='store_true', help='machine-readable result')
    args = parser.parse_args()

    desktop = cursor_desktop_user_dir()
    remote = cursor_remote_user_dir()
    user_dir = desktop if desktop is not None and desktop.is_dir() else remote
    if user_dir is None or not user_dir.is_dir():
        payload = {
            'ok': True,
            'skipped': True,
            'reason': t('cursor_browser_policy_no_cursor'),
            'changed': [],
            'unchanged': [],
        }
        _print_result(payload, as_json=args.json)
        return 0

    db_path = cursor_state_db(user_dir)
    if not db_path.is_file():
        reason_key = (
            'cursor_browser_policy_remote_only'
            if (desktop is None or not desktop.is_dir()) and remote is not None
            else 'cursor_browser_policy_no_db'
        )
        payload = {
            'ok': True,
            'skipped': True,
            'reason': t(reason_key),
            'changed': [],
            'unchanged': [],
        }
        _print_result(payload, as_json=args.json)
        return 0

    try:
        payload = apply_browser_policy(db_path)
    except sqlite3.Error as exc:
        print(
            format_console('error', t('cursor_browser_policy_db_error', exc=exc)),
            file=sys.stderr,
        )
        return 1

    _print_result(payload, as_json=args.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
