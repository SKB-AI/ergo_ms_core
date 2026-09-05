#!/usr/bin/env python3
"""
Учётные данные и адрес сайта для встроенного браузера Cursor.

Читает ADMIN_LOGIN / ADMIN_PASSWORD из .env и env/mcp.env.
Хук sessionStart подставляет инструкцию входа без пароля в контекст агента.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from env_file_loader import load_project_env  # noqa: E402

_TRUTHY = ('1', 'true', 'yes', 'on')
_LOGIN_PATH = '/login'


def _truthy(value: str | None) -> bool:
    return (value or '').strip().lower() in _TRUTHY


def resolve_site_url(env: Mapping[str, str]) -> str:
    """Публичный origin клиента: FRONTEND_BASE_URL, иначе nginx, иначе CLIENT_*."""
    explicit = (env.get('FRONTEND_BASE_URL') or '').strip().rstrip('/')
    if explicit:
        return explicit

    proxy = (env.get('ERGO_PROXY') or '').strip().lower()
    host = (
        (env.get('NGINX_PUBLIC_HOST') or '').strip()
        or (env.get('NGINX_SERVER_NAME') or '').strip()
    )
    if proxy == 'nginx' and host:
        scheme = 'https' if _truthy(env.get('NGINX_USE_HTTPS')) else 'http'
        return f'{scheme}://{host}'

    client_host = (env.get('CLIENT_HOST') or 'localhost').strip() or 'localhost'
    client_port = (env.get('CLIENT_PORT') or '8001').strip() or '8001'
    return f'http://{client_host}:{client_port}'


def resolve_login_url(env: Mapping[str, str]) -> str:
    return resolve_site_url(env).rstrip('/') + _LOGIN_PATH


def load_browser_auth(root: Path | None = None) -> dict[str, str]:
    project_root = root if root is not None else _PROJECT_ROOT
    env = load_project_env(project_root)
    login = (env.get('ADMIN_LOGIN') or '').strip()
    password = (env.get('ADMIN_PASSWORD') or '').strip()
    site_url = resolve_site_url(env)
    return {
        'login': login,
        'password': password,
        'site_url': site_url,
        'login_url': site_url.rstrip('/') + _LOGIN_PATH,
    }


def public_auth_payload(auth: Mapping[str, str]) -> dict[str, object]:
    """Метаданные для агента и тестов. Пароль сюда не попадает."""
    login = (auth.get('login') or '').strip()
    password = (auth.get('password') or '').strip()
    return {
        'login': login,
        'password_set': bool(password),
        'site_url': auth.get('site_url') or '',
        'login_url': auth.get('login_url') or '',
        'login_field': '#login',
        'password_field': '#password',
        'submit': 'button[type="submit"]',
    }


def build_agent_context(auth: Mapping[str, str]) -> str:
    public = public_auth_payload(auth)
    login = str(public['login'])
    login_url = str(public['login_url'])
    site_url = str(public['site_url'])
    password_set = bool(public['password_set'])

    lines = [
        'Встроенный браузер Cursor (cursor-ide-browser): если открылась форма входа ERGO MS, войди сам.',
        f'Сайт: {site_url}. Страница входа: {login_url}.',
        'Поля формы: #login, #password, кнопка button[type="submit"]. Успех — URL с /home, боковое меню.',
        'Логин и пароль бери из ADMIN_LOGIN и ADMIN_PASSWORD в env/mcp.env (или корневой .env).',
        'Прочитай файл сам. Не спрашивай пароль у пользователя и не печатай его в чат.',
        'Не подставляй JWT в localStorage. Не выдумывай другую учётку.',
    ]
    if login:
        lines.append(f'ADMIN_LOGIN сейчас: {login}.')
    else:
        lines.append(
            'ADMIN_LOGIN пуст. Задай его в env/mcp.env и создай пользователя: '
            'ergoms api createsuperuser --noinput.',
        )
    if not password_set:
        lines.append(
            'ADMIN_PASSWORD пуст. Запиши пароль суперпользователя в env/mcp.env.',
        )
    return '\n'.join(lines)


def hook_payload(root: Path | None = None) -> dict[str, object]:
    auth = load_browser_auth(root)
    return {'additional_context': build_agent_context(auth)}


def _read_hook_stdin() -> None:
    if sys.stdin.isatty():
        return
    try:
        sys.stdin.read()
    except OSError:
        return


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Cursor browser login context from ADMIN_LOGIN / ADMIN_PASSWORD',
    )
    parser.add_argument(
        '--hook',
        action='store_true',
        help='sessionStart JSON: additional_context without the password',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='machine-readable metadata without the password',
    )
    parser.add_argument(
        '--root',
        type=Path,
        default=None,
        help='project root (default: repository root)',
    )
    args = parser.parse_args()

    auth = load_browser_auth(args.root)
    if args.hook:
        _read_hook_stdin()
        print(json.dumps(hook_payload(args.root), ensure_ascii=False))
        return 0
    if args.json:
        print(json.dumps(public_auth_payload(auth), ensure_ascii=False))
        return 0

    public = public_auth_payload(auth)
    print(build_agent_context(auth))
    if not public['login'] or not public['password_set']:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
