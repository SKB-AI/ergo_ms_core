"""Бюджет Celery: totals хоста/cgroup минус резерв инфраструктуры."""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _DEPLOYMENT_DIR / 'scripts'
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from ergo_process_classifier import ProcessSample, iter_ergo_processes  # noqa: E402
from process_roles_loader import load_module_process_roles  # noqa: E402

from celery_balance.constants import CELERY_WORKER_ROLE, RESERVE_ROLES
from celery_balance.gpu import (
    GpuSnapshot,
    apply_vram_reserve,
    probe_compute_apps,
    probe_gpu,
)
from celery_balance.settings import BalanceSettings

_CGROUP_MEMORY_MAX = Path('/sys/fs/cgroup/memory.max')
_CGROUP_MEMORY_V1 = Path('/sys/fs/cgroup/memory/memory.limit_in_bytes')
_CGROUP_CPU_MAX = Path('/sys/fs/cgroup/cpu.max')
_CGROUP_CPU_V1_QUOTA = Path('/sys/fs/cgroup/cpu/cpu.cfs_quota_us')
_CGROUP_CPU_V1_PERIOD = Path('/sys/fs/cgroup/cpu/cpu.cfs_period_us')
_DOCKERENV = Path('/.dockerenv')


@dataclass(frozen=True)
class RoleTotals:
    memory_mb: float
    cpu_percent: float
    count: int


@dataclass(frozen=True)
class HostBudget:
    ram_total_mb: float
    ram_used_mb: float
    ram_percent: float
    cpu_count: float
    host_cpu_percent: float
    roles: dict[str, RoleTotals]
    reserve_memory_mb: float
    celery_memory_mb: float
    celery_ram_budget_mb: float
    celery_cpu_budget: float
    gpu: GpuSnapshot
    source: str
    samples: tuple[ProcessSample, ...] = field(default=(), repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            'ram_total_mb': self.ram_total_mb,
            'ram_used_mb': self.ram_used_mb,
            'ram_percent': self.ram_percent,
            'cpu_count': self.cpu_count,
            'host_cpu_percent': self.host_cpu_percent,
            'roles': {
                name: {
                    'memory_mb': item.memory_mb,
                    'cpu_percent': item.cpu_percent,
                    'count': item.count,
                }
                for name, item in sorted(self.roles.items())
            },
            'reserve_memory_mb': self.reserve_memory_mb,
            'celery_memory_mb': self.celery_memory_mb,
            'celery_ram_budget_mb': self.celery_ram_budget_mb,
            'celery_cpu_budget': self.celery_cpu_budget,
            'gpu': {
                'available': self.gpu.available,
                'count': self.gpu.count,
                'vram_total_mb': self.gpu.vram_total_mb,
                'vram_free_mb': self.gpu.vram_free_mb,
                'utilization_pct': self.gpu.utilization_pct,
                'devices': [
                    {
                        'index': item.index,
                        'uuid': item.uuid,
                        'vram_total_mb': item.vram_total_mb,
                        'vram_free_mb': item.vram_free_mb,
                        'utilization_pct': item.utilization_pct,
                    }
                    for item in self.gpu.devices
                ],
            },
            'source': self.source,
        }


def _read_cgroup_memory_mb() -> float | None:
    for path in (_CGROUP_MEMORY_MAX, _CGROUP_MEMORY_V1):
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding='utf-8').strip()
        except OSError:
            continue
        if raw in {'', 'max'}:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # Явно «без лимита» в cgroup v1.
        if value <= 0 or value >= 1 << 62:
            continue
        return round(value / (1024 * 1024), 1)
    return None


def _read_cgroup_cpu_count(host_cpu: float) -> float | None:
    if _CGROUP_CPU_MAX.is_file():
        try:
            quota_raw, period_raw = _CGROUP_CPU_MAX.read_text(encoding='utf-8').split()
        except (OSError, ValueError):
            quota_raw, period_raw = '', ''
        if quota_raw and quota_raw != 'max':
            try:
                quota = float(quota_raw)
                period = float(period_raw)
                if quota > 0 and period > 0:
                    return round(quota / period, 2)
            except ValueError:
                pass
    if _CGROUP_CPU_V1_QUOTA.is_file() and _CGROUP_CPU_V1_PERIOD.is_file():
        try:
            quota = float(_CGROUP_CPU_V1_QUOTA.read_text(encoding='utf-8').strip())
            period = float(_CGROUP_CPU_V1_PERIOD.read_text(encoding='utf-8').strip())
        except (OSError, ValueError):
            return None
        if quota > 0 and period > 0:
            return round(quota / period, 2)
    return None


def _in_container() -> bool:
    if _DOCKERENV.is_file():
        return True
    cgroup = Path('/proc/self/cgroup')
    if cgroup.is_file():
        try:
            return 'docker' in cgroup.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return False
    return False


def _apply_module_vram_reserve(
    gpu: GpuSnapshot,
    samples: list[ProcessSample],
    module_rules,
    *,
    enabled: bool,
) -> GpuSnapshot:
    vram_by_role = {
        rule.role_id: rule.reserve_vram_mb
        for rule in module_rules
        if rule.reserve_vram_mb > 0
    }
    if not vram_by_role:
        return gpu
    live_roles = {item.role for item in samples}
    pids_by_role: dict[str, list[int]] = defaultdict(list)
    for item in samples:
        pids_by_role[item.role].append(item.pid)
    apps = probe_compute_apps(enabled=enabled)
    used_by_pid = {app.pid: app.used_mb for app in apps}
    extra_by_uuid: dict[str, float] = defaultdict(float)
    extra_unassigned = 0.0
    for role_id, reserved in vram_by_role.items():
        if role_id not in live_roles:
            continue
        pids = pids_by_role.get(role_id, [])
        actual = sum(used_by_pid.get(pid, 0.0) for pid in pids)
        extra = max(0.0, reserved - actual)
        if extra <= 0:
            continue
        uuids = [app.gpu_uuid for app in apps if app.pid in set(pids) and app.gpu_uuid]
        if uuids:
            share = extra / len(uuids)
            for uuid in uuids:
                extra_by_uuid[uuid] += share
        else:
            extra_unassigned += extra
    if not extra_by_uuid and extra_unassigned <= 0:
        return gpu
    return apply_vram_reserve(
        gpu,
        extra_by_uuid=dict(extra_by_uuid),
        extra_unassigned_mb=extra_unassigned,
    )


def collect_role_totals(samples: list[ProcessSample]) -> dict[str, RoleTotals]:
    buckets: dict[str, list[ProcessSample]] = defaultdict(list)
    for item in samples:
        buckets[item.role].append(item)
    return {
        role: RoleTotals(
            memory_mb=round(sum(item.memory_mb for item in items), 1),
            cpu_percent=round(sum(item.cpu_percent for item in items), 1),
            count=len(items),
        )
        for role, items in buckets.items()
    }


def sample_budget(
    project_root: Path,
    settings: BalanceSettings,
    *,
    include_gpu: bool = True,
) -> HostBudget:
    vm = psutil.virtual_memory()
    host_ram_total = round(vm.total / (1024 * 1024), 1)
    host_ram_used = round(vm.used / (1024 * 1024), 1)
    cpu_count = float(psutil.cpu_count(logical=True) or 1)
    host_cpu_percent = float(psutil.cpu_percent(interval=0.1))
    source = 'host'

    cgroup_ram = _read_cgroup_memory_mb() if _in_container() else None
    if cgroup_ram is not None and 0 < cgroup_ram < host_ram_total:
        host_ram_total = cgroup_ram
        host_ram_used = min(host_ram_used, host_ram_total)
        source = 'cgroup'
    cgroup_cpu = _read_cgroup_cpu_count(cpu_count) if _in_container() else None
    if cgroup_cpu is not None and 0 < cgroup_cpu < cpu_count:
        cpu_count = cgroup_cpu
        source = 'cgroup'

    samples = list(iter_ergo_processes(project_root))
    roles = collect_role_totals(samples)
    module_rules = load_module_process_roles(str(project_root.resolve()))
    extra_reserve_roles = {
        rule.role_id for rule in module_rules if rule.reserve_host_budget
    }
    reserve_role_ids = RESERVE_ROLES | extra_reserve_roles
    reserve = round(
        sum(item.memory_mb for role, item in roles.items() if role in reserve_role_ids)
        + float(settings.os_reserve_ram_mb),
        1,
    )
    celery_mem = roles.get(
        CELERY_WORKER_ROLE,
        RoleTotals(memory_mb=0.0, cpu_percent=0.0, count=0),
    ).memory_mb
    ram_budget = max(0.0, round(host_ram_total - reserve, 1))
    cpu_budget = max(0.25, round(cpu_count - settings.reserve_cpu, 2))

    gpu_on = include_gpu and settings.gpu_enabled
    gpu = probe_gpu(enabled=gpu_on)
    if gpu.available:
        gpu = _apply_module_vram_reserve(gpu, samples, module_rules, enabled=gpu_on)

    return HostBudget(
        ram_total_mb=host_ram_total,
        ram_used_mb=host_ram_used,
        ram_percent=round(float(vm.percent), 1),
        cpu_count=cpu_count,
        host_cpu_percent=round(host_cpu_percent, 1),
        roles=roles,
        reserve_memory_mb=reserve,
        celery_memory_mb=celery_mem,
        celery_ram_budget_mb=ram_budget,
        celery_cpu_budget=cpu_budget,
        gpu=gpu,
        source=source,
        samples=tuple(samples),
    )
