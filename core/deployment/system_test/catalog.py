"""Какие кейсы входят в какой сьют."""

from __future__ import annotations

from .cases.base import SystemCase
from .cases.browser import BrowserCoreCase
from .cases.commands import CommandsSmokeCase
from .cases.install import InstallFromScratchCase
from .cases.os_services import OsServicesInstalledCase
from .cases.performance import ReadyLatencyCase
from .cases.scenarios import scenario_cases
from .cases.security import SecurityLiveCase

SUITE_UNIT = 'unit'
SUITE_INSTALL = 'install'
SUITE_COMMANDS = 'commands'
SUITE_SCENARIOS = 'scenarios'
SUITE_SECURITY = 'security'
SUITE_PERFORMANCE = 'performance'
SUITE_BROWSER = 'browser'
SUITE_OS_SERVICES = 'os-services'
SUITE_ALL = 'all'

LIVE_SUITES = (
    SUITE_INSTALL,
    SUITE_COMMANDS,
    SUITE_SCENARIOS,
    SUITE_SECURITY,
    SUITE_PERFORMANCE,
    SUITE_BROWSER,
    SUITE_OS_SERVICES,
)


def cases_for_suite(
    suite: str,
    *,
    spec_ids: list[str] | None = None,
) -> list[SystemCase]:
    if suite == SUITE_INSTALL:
        return [InstallFromScratchCase()]
    if suite == SUITE_COMMANDS:
        return [CommandsSmokeCase()]
    if suite == SUITE_SCENARIOS:
        return scenario_cases(all_specs_mode=False, spec_ids=spec_ids)
    if suite == SUITE_SECURITY:
        return [SecurityLiveCase()]
    if suite == SUITE_PERFORMANCE:
        return [ReadyLatencyCase()]
    if suite == SUITE_BROWSER:
        return [BrowserCoreCase()]
    if suite == SUITE_OS_SERVICES:
        return [OsServicesInstalledCase()]
    if suite == SUITE_ALL:
        items: list[SystemCase] = [
            InstallFromScratchCase(),
            CommandsSmokeCase(),
            SecurityLiveCase(),
            ReadyLatencyCase(),
            BrowserCoreCase(),
            OsServicesInstalledCase(),
        ]
        items.extend(scenario_cases(all_specs_mode=True, spec_ids=spec_ids))
        return items
    return []
