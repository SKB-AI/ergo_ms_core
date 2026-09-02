"""Runtime GPU/VRAM probe (nvidia-smi). Отсутствие GPU — не ошибка."""

from __future__ import annotations

import math
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class GpuDevice:
    index: int
    uuid: str
    vram_total_mb: float
    vram_free_mb: float
    utilization_pct: float


@dataclass(frozen=True)
class GpuComputeApp:
    pid: int
    gpu_uuid: str
    used_mb: float


@dataclass(frozen=True)
class GpuSnapshot:
    available: bool
    count: int
    vram_total_mb: float
    vram_free_mb: float
    utilization_pct: float = 0.0
    devices: tuple[GpuDevice, ...] = ()


def _empty_snapshot() -> GpuSnapshot:
    return GpuSnapshot(
        available=False,
        count=0,
        vram_total_mb=0.0,
        vram_free_mb=0.0,
        utilization_pct=0.0,
        devices=(),
    )


def _nvidia_smi() -> str | None:
    return shutil.which('nvidia-smi')


def probe_gpu(*, enabled: bool = True, timeout_sec: float = 2.0) -> GpuSnapshot:
    empty = _empty_snapshot()
    if not enabled:
        return empty
    binary = _nvidia_smi()
    if not binary:
        return empty
    try:
        result = subprocess.run(
            [
                binary,
                '--query-gpu=index,uuid,memory.total,memory.free,utilization.gpu',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return empty
    if result.returncode != 0 or not result.stdout.strip():
        return empty

    devices: list[GpuDevice] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 5:
            continue
        try:
            index = int(float(parts[0]))
            total = float(parts[2])
            free = float(parts[3])
            util = float(parts[4])
        except ValueError:
            continue
        devices.append(
            GpuDevice(
                index=index,
                uuid=parts[1],
                vram_total_mb=round(total, 1),
                vram_free_mb=round(max(0.0, free), 1),
                utilization_pct=round(max(0.0, min(util, 100.0)), 1),
            )
        )
    if not devices:
        return empty
    total = sum(item.vram_total_mb for item in devices)
    free = sum(item.vram_free_mb for item in devices)
    util = sum(item.utilization_pct for item in devices) / len(devices)
    return GpuSnapshot(
        available=True,
        count=len(devices),
        vram_total_mb=round(total, 1),
        vram_free_mb=round(free, 1),
        utilization_pct=round(util, 1),
        devices=tuple(devices),
    )


def probe_compute_apps(*, enabled: bool = True, timeout_sec: float = 2.0) -> tuple[GpuComputeApp, ...]:
    if not enabled:
        return ()
    binary = _nvidia_smi()
    if not binary:
        return ()
    try:
        result = subprocess.run(
            [
                binary,
                '--query-compute-apps=pid,gpu_uuid,used_gpu_memory',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0 or not result.stdout.strip():
        return ()
    apps: list[GpuComputeApp] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 3:
            continue
        try:
            pid = int(float(parts[0]))
            used = float(parts[2])
        except ValueError:
            continue
        apps.append(GpuComputeApp(pid=pid, gpu_uuid=parts[1], used_mb=round(max(0.0, used), 1)))
    return tuple(apps)


def iter_devices(snapshot: GpuSnapshot) -> tuple[GpuDevice, ...]:
    if snapshot.devices:
        return snapshot.devices
    if snapshot.available:
        return (
            GpuDevice(
                index=0,
                uuid='',
                vram_total_mb=snapshot.vram_total_mb,
                vram_free_mb=snapshot.vram_free_mb,
                utilization_pct=snapshot.utilization_pct,
            ),
        )
    return ()


def gpu_slots_for_need(snapshot: GpuSnapshot, vram_need: float) -> int:
    if not snapshot.available:
        return 0
    if vram_need <= 0:
        return 1
    return sum(math.floor(device.vram_free_mb / vram_need) for device in iter_devices(snapshot))


def apply_vram_reserve(
    snapshot: GpuSnapshot,
    *,
    extra_by_uuid: dict[str, float] | None = None,
    extra_unassigned_mb: float = 0.0,
) -> GpuSnapshot:
    """Уменьшает свободную VRAM на заявленный запас демонов (сверх уже занятого)."""
    devices = list(iter_devices(snapshot))
    if not devices:
        return snapshot
    extra_by_uuid = extra_by_uuid or {}
    free_map = {device.index: device.vram_free_mb for device in devices}
    uuid_to_index = {device.uuid: device.index for device in devices if device.uuid}

    for uuid, extra in extra_by_uuid.items():
        if extra <= 0:
            continue
        index = uuid_to_index.get(uuid)
        if index is None:
            extra_unassigned_mb += extra
            continue
        take = min(free_map[index], extra)
        free_map[index] = round(free_map[index] - take, 1)

    remaining = max(0.0, extra_unassigned_mb)
    for device in sorted(devices, key=lambda item: free_map[item.index], reverse=True):
        if remaining <= 0:
            break
        take = min(free_map[device.index], remaining)
        free_map[device.index] = round(free_map[device.index] - take, 1)
        remaining -= take

    new_devices = tuple(
        GpuDevice(
            index=device.index,
            uuid=device.uuid,
            vram_total_mb=device.vram_total_mb,
            vram_free_mb=max(0.0, free_map[device.index]),
            utilization_pct=device.utilization_pct,
        )
        for device in devices
    )
    return GpuSnapshot(
        available=True,
        count=len(new_devices),
        vram_total_mb=round(sum(item.vram_total_mb for item in new_devices), 1),
        vram_free_mb=round(sum(item.vram_free_mb for item in new_devices), 1),
        utilization_pct=snapshot.utilization_pct,
        devices=new_devices,
    )


def consume_gpu_slots(
    free_by_index: dict[int, float],
    vram_need: float,
    slots: int,
) -> None:
    """Списывает VRAM слотов с карт (для общего бюджета между воркерами)."""
    if vram_need <= 0 or slots <= 0:
        return
    remaining = slots
    for index in sorted(free_by_index, key=lambda key: free_by_index[key], reverse=True):
        while remaining > 0 and free_by_index[index] >= vram_need:
            free_by_index[index] = round(free_by_index[index] - vram_need, 1)
            remaining -= 1
        if remaining <= 0:
            return
