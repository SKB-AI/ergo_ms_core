"""Расчёт concurrency / prefetch / autoscale в пределах общего бюджета."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from celery_balance.constants import (
    CLASS_HEAVY,
    CLASS_LIGHT,
    DEFAULT_PREFETCH,
    DEFAULT_RESERVE_LIGHT,
    HEAVY_PREFETCH,
)
from celery_balance.footprint_loader import (
    FootprintCatalog,
    TaskFootprint,
    resolve_footprint,
)
from celery_balance.gpu import consume_gpu_slots, gpu_slots_for_need, iter_devices
from celery_balance.history import HistoryStats
from celery_balance.host_budget import HostBudget
from celery_balance.queues import QueueSnapshot
from celery_balance.settings import BalanceSettings
from celery_balance.workers import WorkerSpec, resolve_worker_queues


@dataclass
class WorkerPlan:
    name: str
    hostname: str
    queues: list[str]
    pool: str
    yaml_concurrency: int | None
    concurrency: int
    prefetch_multiplier: int
    autoscale_min: int | None
    autoscale_max: int | None
    gpu_required: bool
    reasons: list[str] = field(default_factory=list)
    pause_queues: list[str] = field(default_factory=list)
    queue_limits: dict[str, int] = field(default_factory=dict)
    queue_classes: dict[str, str] = field(default_factory=dict)
    reserve_light: int = 0
    non_light_cap: int = 0
    mixed_gpu_light: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'hostname': self.hostname,
            'queues': self.queues,
            'pool': self.pool,
            'yaml_concurrency': self.yaml_concurrency,
            'concurrency': self.concurrency,
            'prefetch_multiplier': self.prefetch_multiplier,
            'autoscale_min': self.autoscale_min,
            'autoscale_max': self.autoscale_max,
            'gpu_required': self.gpu_required,
            'reasons': list(self.reasons),
            'pause_queues': list(self.pause_queues),
            'queue_limits': dict(self.queue_limits),
            'queue_classes': dict(self.queue_classes),
            'reserve_light': self.reserve_light,
            'non_light_cap': self.non_light_cap,
            'mixed_gpu_light': self.mixed_gpu_light,
        }


@dataclass
class _WorkerDraft:
    worker: WorkerSpec
    queues: list[str]
    specs: list[TaskFootprint]
    avg_ram: float
    avg_cpu: float
    vram_need: float
    gpu_required: bool
    cpu_fallback: bool
    max_parallel: int
    hard_max: int
    depth_score: int
    heavy: bool
    mixed_fairness: bool
    mixed_gpu_light: bool
    reasons: list[str]


def _effective_footprint(
    declared: TaskFootprint,
    history: dict[str, HistoryStats],
) -> TaskFootprint:
    stats = history.get(declared.queue)
    ram_mb = declared.ram_mb
    vram_mb = declared.vram_mb
    if stats is not None:
        if stats.ram_mb_median is not None:
            ram_mb = max(declared.ram_mb, stats.ram_mb_median)
        if stats.vram_mb_median is not None:
            vram_mb = max(declared.vram_mb, stats.vram_mb_median)
    if ram_mb == declared.ram_mb and vram_mb == declared.vram_mb:
        return declared
    return TaskFootprint(
        queue=declared.queue,
        pattern=declared.pattern,
        task_class=declared.task_class,
        ram_mb=ram_mb,
        cpu_units=declared.cpu_units,
        gpu_required=declared.gpu_required,
        vram_mb=vram_mb,
        max_parallel=declared.max_parallel,
        cpu_fallback=declared.cpu_fallback,
        module=declared.module,
    )


def _weighted_average(
    specs: list[TaskFootprint],
    depths: dict[str, int],
    attr: str,
) -> float:
    weight_sum = 0.0
    value_sum = 0.0
    for spec in specs:
        weight = max(1, depths.get(spec.queue, 0))
        value_sum += float(getattr(spec, attr)) * weight
        weight_sum += weight
    if weight_sum <= 0:
        return float(getattr(specs[0], attr)) if specs else 1.0
    return value_sum / weight_sum


def _hard_max(settings: BalanceSettings, budget: HostBudget) -> int:
    if settings.max_concurrency is not None:
        return settings.max_concurrency
    return max(1, int(math.floor(budget.cpu_count * 2)))


def _draft_worker(
    worker: WorkerSpec,
    *,
    all_queues: list[str],
    catalog: FootprintCatalog,
    budget: HostBudget,
    settings: BalanceSettings,
    depths: dict[str, int],
    history: dict[str, HistoryStats],
) -> _WorkerDraft:
    queues = resolve_worker_queues(worker.queues, all_queues)
    if not queues:
        queues = ['default']
    specs = [
        _effective_footprint(resolve_footprint(name, catalog), history)
        for name in queues
    ]
    reasons: list[str] = []
    avg_ram = _weighted_average(specs, depths, 'ram_mb')
    avg_cpu = _weighted_average(specs, depths, 'cpu_units')
    gpu_required = any(item.gpu_required for item in specs)
    cpu_fallback = all(item.cpu_fallback for item in specs if item.gpu_required) if gpu_required else True
    vram_need = max((item.vram_mb for item in specs if item.gpu_required), default=0.0)
    max_parallel = min(item.max_parallel for item in specs)
    hard_max = _hard_max(settings, budget)
    heavy = any(item.task_class == CLASS_HEAVY or item.gpu_required for item in specs)
    light_present = any(item.task_class == CLASS_LIGHT for item in specs)
    non_light_present = any(item.task_class != CLASS_LIGHT for item in specs)
    serves_all = worker.queues == 'all' or worker.queues is None
    mixed_fairness = (serves_all or len(queues) > 1) and light_present and non_light_present
    gpu_light = any(not item.gpu_required and item.task_class != CLASS_HEAVY for item in specs)
    mixed_gpu = gpu_required and (gpu_light or len(queues) > 1)
    depth_score = max((depths.get(name, 0) for name in queues), default=0)
    reasons.append(f'hard_cap={hard_max}')
    return _WorkerDraft(
        worker=worker,
        queues=queues,
        specs=specs,
        avg_ram=avg_ram,
        avg_cpu=avg_cpu,
        vram_need=vram_need,
        gpu_required=gpu_required,
        cpu_fallback=cpu_fallback,
        max_parallel=max_parallel,
        hard_max=hard_max,
        depth_score=depth_score,
        heavy=heavy,
        mixed_fairness=mixed_fairness,
        mixed_gpu_light=mixed_gpu,
        reasons=reasons,
    )


def _gpu_slots_from_free(
    free_by_index: dict[int, float],
    vram_need: float,
    snapshot_available: bool,
) -> int:
    if not snapshot_available:
        return 0
    if vram_need <= 0:
        return 1
    return sum(math.floor(free / vram_need) for free in free_by_index.values())


def _finish_plan(
    draft: _WorkerDraft,
    *,
    concurrency: int,
    settings: BalanceSettings,
    pending_sec: float,
    extra_reasons: list[str],
    pause_queues: list[str],
) -> WorkerPlan:
    reasons = list(draft.reasons)
    reasons.extend(extra_reasons)
    if pending_sec > 120 and concurrency < draft.hard_max:
        reasons.append(f'forecast pending {pending_sec:.0f}s (no extra slots above budget)')
    mixed = draft.mixed_fairness
    prefetch = HEAVY_PREFETCH if draft.heavy or mixed else DEFAULT_PREFETCH
    if draft.heavy:
        reasons.append('prefetch=1 (heavy/gpu)')
    elif mixed:
        reasons.append('prefetch=1 (mixed queues)')
    autoscale_min: int | None = None
    autoscale_max: int | None = None
    pool = (draft.worker.pool or 'threads').strip().lower()
    if pool == 'prefork' and concurrency > settings.min_concurrency:
        autoscale_min = settings.min_concurrency
        autoscale_max = concurrency
        reasons.append(f'autoscale={autoscale_max},{autoscale_min} (prefork)')
    queue_limits: dict[str, int] = {}
    queue_classes: dict[str, str] = {}
    paused = set(pause_queues)
    for spec in draft.specs:
        queue_classes[spec.queue] = spec.task_class
        if spec.queue in paused:
            queue_limits[spec.queue] = 0
        else:
            queue_limits[spec.queue] = max(1, min(concurrency, spec.max_parallel))
    reserve_light = 0
    non_light_cap = 0
    if mixed and concurrency > 1:
        reserve_light = min(DEFAULT_RESERVE_LIGHT, concurrency - 1)
        non_light_cap = concurrency - reserve_light
        reasons.append(f'reserve_light={reserve_light}')
        reasons.append(f'non_light_cap={non_light_cap}')
    return WorkerPlan(
        name=draft.worker.name,
        hostname=draft.worker.hostname,
        queues=draft.queues,
        pool=pool,
        yaml_concurrency=draft.worker.concurrency,
        concurrency=concurrency,
        prefetch_multiplier=prefetch,
        autoscale_min=autoscale_min,
        autoscale_max=autoscale_max,
        gpu_required=draft.gpu_required,
        reasons=reasons,
        pause_queues=list(pause_queues),
        queue_limits=queue_limits,
        queue_classes=queue_classes,
        reserve_light=reserve_light,
        non_light_cap=non_light_cap,
        mixed_gpu_light=draft.mixed_gpu_light,
    )


def plan_worker(
    worker: WorkerSpec,
    *,
    all_queues: list[str],
    catalog: FootprintCatalog,
    budget: HostBudget,
    settings: BalanceSettings,
    depths: dict[str, int],
    history: dict[str, HistoryStats],
    pending_sec: float,
    previous: dict[str, int] | None = None,
) -> WorkerPlan:
    plans = plan_all(
        (worker,),
        all_queues=all_queues,
        catalog=catalog,
        budget=budget,
        settings=settings,
        queue_snapshots=tuple(
            QueueSnapshot(name=name, depth=depths.get(name), source='plan')
            for name in depths
        ) if depths else (),
        history=history,
        pending_sec=pending_sec,
        previous=previous or {},
    )
    return plans[0]


def plan_all(
    workers: tuple[WorkerSpec, ...],
    *,
    all_queues: list[str],
    catalog: FootprintCatalog,
    budget: HostBudget,
    settings: BalanceSettings,
    queue_snapshots: tuple[QueueSnapshot, ...],
    history: dict[str, HistoryStats],
    pending_sec: float,
    previous: dict[str, int] | None = None,
) -> list[WorkerPlan]:
    depths = {
        item.name: int(item.depth or 0)
        for item in queue_snapshots
        if item.depth is not None
    }
    previous = previous or {}
    drafts = [
        _draft_worker(
            worker,
            all_queues=all_queues,
            catalog=catalog,
            budget=budget,
            settings=settings,
            depths=depths,
            history=history,
        )
        for worker in workers
    ]
    drafts.sort(
        key=lambda item: (
            0 if item.gpu_required else 1,
            0 if item.heavy else 1,
            -item.depth_score,
            item.worker.name,
        )
    )

    remaining_ram = float(budget.celery_ram_budget_mb)
    remaining_cpu = float(budget.celery_cpu_budget)
    free_by_index = {
        device.index: device.vram_free_mb for device in iter_devices(budget.gpu)
    }
    util_hot = (
        budget.gpu.available
        and settings.gpu_util_cap > 0
        and budget.gpu.utilization_pct >= settings.gpu_util_cap
    )

    planned: dict[str, WorkerPlan] = {}
    for draft in drafts:
        extra: list[str] = []
        ram_slots = (
            math.floor(remaining_ram / draft.avg_ram) if draft.avg_ram > 0 else 1
        )
        cpu_slots = (
            math.floor(remaining_cpu / draft.avg_cpu) if draft.avg_cpu > 0 else 1
        )
        extra.append(
            f'budget RAM {remaining_ram:.0f} MB / avg {draft.avg_ram:.0f} MB → {ram_slots}'
        )
        extra.append(
            f'budget CPU {remaining_cpu:.2f} / avg {draft.avg_cpu:.2f} → {cpu_slots}'
        )

        gpu_slots = ram_slots
        if draft.gpu_required:
            if not budget.gpu.available:
                gpu_slots = 0
                extra.append('GPU required but none detected')
            elif draft.vram_need <= 0:
                gpu_slots = 1 if gpu_slots_for_need(budget.gpu, 0) else 0
                extra.append('GPU required, vram_mb not declared → 1 slot')
            else:
                gpu_slots = _gpu_slots_from_free(
                    free_by_index, draft.vram_need, budget.gpu.available
                )
                extra.append(
                    f'VRAM per-GPU / {draft.vram_need:.0f} → {gpu_slots}'
                )
            if util_hot and gpu_slots > 0:
                prev = max(1, int(previous.get(draft.worker.name, 1)))
                capped = min(gpu_slots, prev)
                if capped < gpu_slots:
                    extra.append(
                        f'GPU util {budget.gpu.utilization_pct:.0f}% ≥ '
                        f'{settings.gpu_util_cap:.0f}% → keep {capped}'
                    )
                gpu_slots = capped

        raw = min(ram_slots, cpu_slots, draft.hard_max)
        if draft.gpu_required:
            raw = min(raw, max(gpu_slots, 0))

        pause_queues: list[str] = []
        concurrency = max(settings.min_concurrency, raw)
        if draft.gpu_required and gpu_slots <= 0:
            concurrency = settings.min_concurrency
            extra.append(f'keep min_concurrency={concurrency} for GPU queue without VRAM')
            if not draft.cpu_fallback:
                pause_queues = [spec.queue for spec in draft.specs if spec.gpu_required]
                extra.append('pause_queue (gpu_required, no device, cpu_fallback=false)')

        remaining_ram = max(0.0, remaining_ram - concurrency * draft.avg_ram)
        remaining_cpu = max(0.0, remaining_cpu - concurrency * draft.avg_cpu)
        if draft.gpu_required and gpu_slots > 0 and draft.vram_need > 0:
            consume_gpu_slots(free_by_index, draft.vram_need, concurrency)

        planned[draft.worker.name] = _finish_plan(
            draft,
            concurrency=concurrency,
            settings=settings,
            pending_sec=pending_sec,
            extra_reasons=extra,
            pause_queues=pause_queues,
        )

    return [planned[worker.name] for worker in workers]
