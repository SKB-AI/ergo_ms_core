"""Оркестратор системного сьюта."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from cli_locale import t
from console_tags import format_console
from project_layout import cache_system_test_dir, ensure_dir
from scenario_test.isolation import workspace_config_fingerprint

from .catalog import SUITE_ALL, SUITE_OS_SERVICES, SUITE_SCENARIOS, cases_for_suite
from .cases.base import SystemCase
from .docker_env import DockerEnvironment, SkipEnvironment
from .environment import IsolatedEnvironment
from .os_services_env import OsServicesEnvironment, SkipOsServices
from .report import CaseResult, SuiteReport
from .worktree_env import HostWorktreeEnvironment


class SystemSuite:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(
        self,
        *,
        suite: str,
        launch: str,
        spec_ids: list[str] | None = None,
    ) -> SuiteReport:
        report = SuiteReport()
        cases = cases_for_suite(suite, spec_ids=spec_ids)
        if suite == SUITE_SCENARIOS:
            for case in cases:
                report.add(self._run_scenario_case(case))
            return report
        env_cases = [case for case in cases if case.domain != 'scenarios']
        scenario_items = [case for case in cases if case.domain == 'scenarios']
        if suite == SUITE_ALL and not env_cases:
            env_cases = cases
            scenario_items = []
        env = self._build_env(launch, suite)
        fingerprint = workspace_config_fingerprint(self.workspace)
        try:
            try:
                env.provision()
                env.start()
            except SkipEnvironment as exc:
                report.add(CaseResult('environment', suite, 'skip', str(exc)))
            except SkipOsServices as exc:
                report.add(CaseResult('environment', suite, 'skip', str(exc)))
            except Exception as exc:
                report.add(CaseResult('environment', suite, 'fail', str(exc)))
            else:
                for case in env_cases:
                    if not case.applies_to(env.kind):
                        report.add(CaseResult(case.name, case.domain, 'skip', f'не для {env.kind}'))
                        continue
                    report.add(self._run_case(case, env))
        finally:
            try:
                env.teardown()
            except Exception as exc:
                report.add(CaseResult('teardown', suite, 'fail', str(exc)))
            if workspace_config_fingerprint(self.workspace) != fingerprint:
                report.add(CaseResult(
                    'workspace_guard',
                    suite,
                    'fail',
                    t('scenario_test_host_config_changed', id=suite),
                ))
        for case in scenario_items:
            report.add(self._run_scenario_case(case))
        return report

    def _run_scenario_case(self, case: SystemCase) -> CaseResult:
        # ScenarioSpecCase сам поднимает изолированный стек scenario_test.
        env = self._build_env('host', case.domain)
        started = time.perf_counter()
        try:
            result = case.run(env)
        except Exception as exc:
            result = CaseResult(case.name, case.domain, 'fail', str(exc))
        result.duration_s = time.perf_counter() - started
        return result

    def _run_case(self, case: SystemCase, env: IsolatedEnvironment) -> CaseResult:
        started = time.perf_counter()
        try:
            result = case.run(env)
        except Exception as exc:
            result = CaseResult(case.name, case.domain, 'fail', str(exc))
        result.duration_s = time.perf_counter() - started
        return result

    def _build_env(self, launch: str, suite: str) -> IsolatedEnvironment:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        suffix = secrets.token_hex(3)
        prefix = f'ergo_st_{suffix}'
        run_dir = ensure_dir(cache_system_test_dir(self.workspace) / stamp / f'{launch}_{suffix}')
        if launch == 'os-services' or suite == SUITE_OS_SERVICES:
            return OsServicesEnvironment(self.workspace, run_dir, prefix)
        if launch == 'docker':
            spec = 'docker_direct'
            return DockerEnvironment(self.workspace, run_dir, prefix, spec_id=spec)
        return HostWorktreeEnvironment(self.workspace, run_dir, prefix)


def print_report(report: SuiteReport) -> None:
    for item in report.results:
        level = 'ok' if item.status == 'ok' else ('skip' if item.status == 'skip' else 'error')
        print(format_console(level, f'{item.domain}/{item.name}: {item.detail or item.status}'))
