"""Живые проверки безопасности на throwaway-стеке."""

from __future__ import annotations

from ..environment import IsolatedEnvironment
from ..http import http_exchange
from ..report import CaseResult
from .base import SystemCase

_SECRET_MARKERS = (
    'API_SECRET_KEY',
    'API_JWT_SIGNING_KEY',
    'SECRET_KEY',
)


class SecurityLiveCase(SystemCase):
    name = 'security_live'
    domain = 'security'

    def run(self, env: IsolatedEnvironment) -> CaseResult:
        env.ensure_api()
        check = env.run_ergoms('security-check', timeout=180)
        if check.returncode >= 2:
            return CaseResult(self.name, self.domain, 'fail', 'security-check failed')
        env_file = env.tree_root / '.env'
        if env_file.is_file():
            raw = env_file.read_text(encoding='utf-8')
            if _debug_enabled(raw):
                return CaseResult(self.name, self.domain, 'fail', 'DEBUG включён в throwaway .env')
        url = env.http_base().rstrip('/') + '/api/cms/adp/profile/'
        status, headers, body = http_exchange(url)
        if status not in (401, 403):
            return CaseResult(
                self.name,
                self.domain,
                'fail',
                f'без JWT ожидался 401/403, получен {status}',
            )
        for marker in _SECRET_MARKERS:
            if marker in body:
                return CaseResult(self.name, self.domain, 'fail', f'в ответе есть {marker}')
        notes = [f'anon={status}']
        if _nginx_enabled(env) and 'content-security-policy' not in headers:
            return CaseResult(self.name, self.domain, 'fail', 'за nginx нет Content-Security-Policy')
        if 'content-security-policy' in headers:
            notes.append('csp')
        return CaseResult(self.name, self.domain, 'ok', ', '.join(notes))


def _debug_enabled(env_text: str) -> bool:
    for line in env_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('#') or '=' not in stripped:
            continue
        key, value = stripped.split('=', 1)
        if key.strip() == 'DEBUG':
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return False


def _nginx_enabled(env: IsolatedEnvironment) -> bool:
    env_file = env.tree_root / '.env'
    if not env_file.is_file():
        return False
    text = env_file.read_text(encoding='utf-8')
    return 'ERGO_PROXY=nginx' in text or 'NGINX_ENABLED=true' in text
