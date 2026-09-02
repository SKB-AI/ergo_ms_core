"""Worktree + живые службы ОС с тестовым префиксом."""

from __future__ import annotations

from .environment import has_os_service_privilege
from .http import wait_http
from .worktree_env import HostWorktreeEnvironment


class OsServicesEnvironment(HostWorktreeEnvironment):
    kind = 'os-services'

    def provision(self) -> None:
        if not has_os_service_privilege():
            raise SkipOsServices('нет прав администратора / sudo — службы ОС не ставим')
        super().provision()

    def start(self) -> None:
        result = self.run_ergoms('install-services', timeout=3600)
        if result.returncode != 0:
            detail = ((result.stderr or '') + '\n' + (result.stdout or ''))[-2000:]
            raise RuntimeError(detail or f'install-services exit {result.returncode}')
        wait_http(self.http_base(), timeout_sec=180.0)

    def start_client(self) -> None:
        wait_http(self.client_base(), timeout_sec=120.0, path='/')

    def teardown(self) -> None:
        try:
            self.run_ergoms('uninstall-services', timeout=1800)
        except Exception:
            pass
        super().teardown()


class SkipOsServices(RuntimeError):
    pass
