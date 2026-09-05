from __future__ import annotations

import json
import unittest

import _bootstrap  # noqa: F401

from cursor_browser_auth import (  # noqa: E402
    build_agent_context,
    hook_payload,
    public_auth_payload,
    resolve_login_url,
    resolve_site_url,
)


class CursorBrowserAuthTests(unittest.TestCase):
    def test_resolve_site_url_frontend_base(self) -> None:
        url = resolve_site_url({
            'FRONTEND_BASE_URL': 'https://app.example.com/',
            'ERGO_PROXY': 'nginx',
            'NGINX_SERVER_NAME': 'other.example.com',
        })
        self.assertEqual(url, 'https://app.example.com')

    def test_resolve_site_url_nginx_http(self) -> None:
        url = resolve_site_url({
            'ERGO_PROXY': 'nginx',
            'NGINX_PUBLIC_HOST': 'app.example.com',
            'NGINX_USE_HTTPS': 'false',
        })
        self.assertEqual(url, 'http://app.example.com')

    def test_resolve_site_url_nginx_https(self) -> None:
        url = resolve_site_url({
            'ERGO_PROXY': 'nginx',
            'NGINX_SERVER_NAME': 'app.example.com',
            'NGINX_USE_HTTPS': 'true',
        })
        self.assertEqual(url, 'https://app.example.com')

    def test_resolve_site_url_client_ports(self) -> None:
        url = resolve_site_url({
            'ERGO_PROXY': 'none',
            'CLIENT_HOST': '127.0.0.1',
            'CLIENT_PORT': '8001',
        })
        self.assertEqual(url, 'http://127.0.0.1:8001')

    def test_resolve_login_url(self) -> None:
        url = resolve_login_url({
            'FRONTEND_BASE_URL': 'http://app.example.com',
        })
        self.assertEqual(url, 'http://app.example.com/login')

    def test_public_payload_hides_password(self) -> None:
        payload = public_auth_payload({
            'login': 'admin',
            'password': 'secret-value',
            'site_url': 'http://app.example.com',
            'login_url': 'http://app.example.com/login',
        })
        dumped = json.dumps(payload)
        self.assertTrue(payload['password_set'])
        self.assertEqual(payload['login'], 'admin')
        self.assertNotIn('secret-value', dumped)
        self.assertNotIn('password', payload)

    def test_agent_context_hides_password(self) -> None:
        text = build_agent_context({
            'login': 'admin',
            'password': 'secret-value',
            'site_url': 'http://app.example.com',
            'login_url': 'http://app.example.com/login',
        })
        self.assertIn('ADMIN_LOGIN сейчас: admin.', text)
        self.assertIn('#login', text)
        self.assertNotIn('secret-value', text)

    def test_hook_payload_has_context(self) -> None:
        payload = hook_payload()
        self.assertIn('additional_context', payload)
        self.assertIsInstance(payload['additional_context'], str)
        self.assertIn('env/mcp.env', str(payload['additional_context']))


if __name__ == '__main__':
    unittest.main()
