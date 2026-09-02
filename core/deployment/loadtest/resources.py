"""
Снимок ресурсов хоста и процессов ERGO MS для find-limit.

Одинаково на Windows и Linux (psutil).
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _DEPLOYMENT_DIR / 'scripts'
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import psutil  # noqa: E402

from ergo_process_classifier import iter_ergo_processes  # noqa: E402

# Роли, чья нагрузка ближе к API под find-limit (не client/nginx/postgres).
_LOAD_ROLES = frozenset({
    'api',
    'media-api',
    'celery-worker',
    'celery-beat',
})

HOST_CPU_SAMPLE_INTERVAL = 0.2

# Дефолты порогов find-limit (0 = проверка отключена).
DEFAULT_MAX_CPU_PERCENT = 90.0
DEFAULT_MAX_RAM_PERCENT = 90.0
DEFAULT_MAX_ERGO_RAM_MB = 0.0


@dataclass(frozen=True)
class ResourceSample:
    host_cpu_percent: float
    host_ram_percent: float
    host_ram_used_mb: float
    host_ram_total_mb: float
    ergo_memory_mb: float
    ergo_cpu_percent: float
    ergo_process_count: int
    api_memory_mb: float
    api_cpu_percent: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sample_resources(project_root: Path) -> ResourceSample:
    """
    Снять снимок: host CPU/RAM + сумма ERGO-процессов.

    host_cpu — интервальный сэмпл (кросс-платформенно).
    ergo_* — через тот же классификатор, что ergoms resource-usage.
    """
    # Первый вызов cpu_percent часто 0.0 — сразу интервальный сэмпл хоста.
    host_cpu = float(psutil.cpu_percent(interval=HOST_CPU_SAMPLE_INTERVAL))
    vm = psutil.virtual_memory()
    host_ram_percent = float(vm.percent)
    host_ram_used_mb = round(vm.used / (1024 * 1024), 1)
    host_ram_total_mb = round(vm.total / (1024 * 1024), 1)

    ergo_memory = 0.0
    ergo_cpu = 0.0
    ergo_count = 0
    api_memory = 0.0
    api_cpu = 0.0
    try:
        for item in iter_ergo_processes(project_root.resolve()):
            ergo_count += 1
            ergo_memory += float(item.memory_mb)
            ergo_cpu += float(item.cpu_percent)
            if item.role in _LOAD_ROLES:
                api_memory += float(item.memory_mb)
                api_cpu += float(item.cpu_percent)
    except Exception:  # noqa: BLE001 — сэмпл не должен ронять find-limit
        pass

    return ResourceSample(
        host_cpu_percent=round(host_cpu, 1),
        host_ram_percent=round(host_ram_percent, 1),
        host_ram_used_mb=host_ram_used_mb,
        host_ram_total_mb=host_ram_total_mb,
        ergo_memory_mb=round(ergo_memory, 1),
        ergo_cpu_percent=round(ergo_cpu, 1),
        ergo_process_count=ergo_count,
        api_memory_mb=round(api_memory, 1),
        api_cpu_percent=round(api_cpu, 1),
    )
