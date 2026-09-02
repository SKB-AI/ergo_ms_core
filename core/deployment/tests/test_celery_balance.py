"""Планировщик и hook footprint балансировщика Celery (без живого Redis)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from celery_balance.apply import should_skip_change  # noqa: E402
from celery_balance.constants import CLASS_HEAVY, CLASS_LIGHT  # noqa: E402
from celery_balance.footprint_loader import (  # noqa: E402
    FootprintCatalog,
    TaskFootprint,
    load_footprints,
    resolve_footprint,
)
from celery_balance.gpu import GpuDevice, GpuSnapshot, apply_vram_reserve, gpu_slots_for_need  # noqa: E402
from celery_balance.host_budget import HostBudget, RoleTotals  # noqa: E402
from celery_balance.overlay import load_decision, worker_override, write_decision  # noqa: E402
from celery_balance.planner import WorkerPlan, plan_all, plan_worker  # noqa: E402
from celery_balance.settings import BalanceSettings, load_settings  # noqa: E402
from celery_balance.workers import WorkerSpec  # noqa: E402


def _settings(**kwargs) -> BalanceSettings:
    values = dict(
        mode='recommend',
        os_reserve_ram_mb=1024,
        reserve_cpu=1.0,
        min_concurrency=1,
        max_concurrency=None,
        gpu_enabled=True,
        gpu_util_cap=80.0,
        watch_interval_sec=30.0,
        hysteresis_ratio=0.2,
    )
    values.update(kwargs)
    return BalanceSettings(**values)


def _budget(**kwargs) -> HostBudget:
    gpu = kwargs.pop('gpu', GpuSnapshot(False, 0, 0.0, 0.0))
    values = dict(
        ram_total_mb=16384.0,
        ram_used_mb=4000.0,
        ram_percent=25.0,
        cpu_count=8.0,
        host_cpu_percent=10.0,
        roles={'api': RoleTotals(memory_mb=800.0, cpu_percent=5.0, count=1)},
        reserve_memory_mb=1824.0,
        celery_memory_mb=0.0,
        celery_ram_budget_mb=4096.0,
        celery_cpu_budget=6.0,
        gpu=gpu,
        source='host',
    )
    values.update(kwargs)
    return HostBudget(**values)


def _worker(**kwargs) -> WorkerSpec:
    values = dict(
        name='all',
        hostname='all_worker',
        queues=['sample_q'],
        concurrency=8,
        pool='threads',
        loglevel='info',
        description='',
    )
    values.update(kwargs)
    return WorkerSpec(**values)


class PlannerTests(unittest.TestCase):
    def test_ram_budget_caps_concurrency(self) -> None:
        catalog = FootprintCatalog(
            tasks=(),
            by_queue={
                'sample_q': TaskFootprint(
                    queue='sample_q',
                    pattern='',
                    task_class=CLASS_HEAVY,
                    ram_mb=2048,
                    cpu_units=2.0,
                    gpu_required=False,
                    vram_mb=0,
                    max_parallel=8,
                    cpu_fallback=True,
                    module='sample_mod',
                )
            },
        )
        plan = plan_worker(
            _worker(),
            all_queues=['sample_q'],
            catalog=catalog,
            budget=_budget(celery_ram_budget_mb=4096),
            settings=_settings(),
            depths={'sample_q': 1},
            history={},
            pending_sec=0,
        )
        self.assertEqual(plan.concurrency, 2)
        self.assertEqual(plan.prefetch_multiplier, 1)

    def test_hard_cap_and_min(self) -> None:
        catalog = FootprintCatalog(tasks=(), by_queue={})
        plan = plan_worker(
            _worker(queues=['default']),
            all_queues=['default'],
            catalog=catalog,
            budget=_budget(celery_ram_budget_mb=32000, celery_cpu_budget=16),
            settings=_settings(max_concurrency=3, min_concurrency=1),
            depths={},
            history={},
            pending_sec=0,
        )
        self.assertLessEqual(plan.concurrency, 3)

    def test_gpu_without_device_keeps_min_slot(self) -> None:
        catalog = FootprintCatalog(
            tasks=(),
            by_queue={
                'sample_q': TaskFootprint(
                    queue='sample_q',
                    pattern='',
                    task_class=CLASS_HEAVY,
                    ram_mb=1024,
                    cpu_units=1.0,
                    gpu_required=True,
                    vram_mb=4096,
                    max_parallel=4,
                    cpu_fallback=True,
                    module='sample_mod',
                )
            },
        )
        plan = plan_worker(
            _worker(),
            all_queues=['sample_q'],
            catalog=catalog,
            budget=_budget(),
            settings=_settings(min_concurrency=1),
            depths={},
            history={},
            pending_sec=0,
        )
        self.assertEqual(plan.concurrency, 1)
        self.assertTrue(plan.gpu_required)

    def test_gpu_vram_slots(self) -> None:
        catalog = FootprintCatalog(
            tasks=(),
            by_queue={
                'sample_q': TaskFootprint(
                    queue='sample_q',
                    pattern='',
                    task_class=CLASS_HEAVY,
                    ram_mb=256,
                    cpu_units=1.0,
                    gpu_required=True,
                    vram_mb=4096,
                    max_parallel=8,
                    cpu_fallback=True,
                    module='sample_mod',
                )
            },
        )
        plan = plan_worker(
            _worker(),
            all_queues=['sample_q'],
            catalog=catalog,
            budget=_budget(
                celery_ram_budget_mb=16000,
                celery_cpu_budget=8,
                gpu=GpuSnapshot(True, 1, 8192.0, 8192.0),
            ),
            settings=_settings(max_concurrency=16),
            depths={},
            history={},
            pending_sec=0,
        )
        self.assertEqual(plan.concurrency, 2)

    def test_prefork_gets_autoscale(self) -> None:
        plan = plan_worker(
            _worker(pool='prefork', queues=['default']),
            all_queues=['default'],
            catalog=FootprintCatalog(tasks=(), by_queue={}),
            budget=_budget(celery_ram_budget_mb=8000, celery_cpu_budget=6),
            settings=_settings(min_concurrency=1, max_concurrency=8),
            depths={},
            history={},
            pending_sec=0,
        )
        self.assertEqual(plan.pool, 'prefork')
        self.assertIsNotNone(plan.autoscale_max)
        self.assertEqual(plan.autoscale_min, 1)

    def test_shared_ram_budget_across_workers(self) -> None:
        spec = TaskFootprint(
            queue='sample_q',
            pattern='',
            task_class=CLASS_HEAVY,
            ram_mb=2048,
            cpu_units=1.0,
            gpu_required=False,
            vram_mb=0,
            max_parallel=8,
            cpu_fallback=True,
            module='sample_mod',
        )
        catalog = FootprintCatalog(tasks=(), by_queue={'sample_q': spec})
        workers = (
            _worker(name='alpha', hostname='alpha_w'),
            _worker(name='beta', hostname='beta_w'),
        )
        plans = plan_all(
            workers,
            all_queues=['sample_q'],
            catalog=catalog,
            budget=_budget(celery_ram_budget_mb=4096, celery_cpu_budget=16),
            settings=_settings(max_concurrency=16),
            queue_snapshots=(),
            history={},
            pending_sec=0,
        )
        self.assertEqual(sum(item.concurrency for item in plans), 3)
        self.assertEqual(plans[0].concurrency, 2)
        self.assertEqual(plans[1].concurrency, 1)

    def test_two_gpus_do_not_sum_vram(self) -> None:
        catalog = FootprintCatalog(
            tasks=(),
            by_queue={
                'sample_q': TaskFootprint(
                    queue='sample_q',
                    pattern='',
                    task_class=CLASS_HEAVY,
                    ram_mb=256,
                    cpu_units=1.0,
                    gpu_required=True,
                    vram_mb=10240,
                    max_parallel=8,
                    cpu_fallback=True,
                    module='sample_mod',
                )
            },
        )
        gpu = GpuSnapshot(
            True,
            2,
            16384.0,
            16384.0,
            0.0,
            (
                GpuDevice(0, 'gpu-0', 8192.0, 8192.0, 0.0),
                GpuDevice(1, 'gpu-1', 8192.0, 8192.0, 0.0),
            ),
        )
        self.assertEqual(gpu_slots_for_need(gpu, 10240), 0)
        plan = plan_worker(
            _worker(),
            all_queues=['sample_q'],
            catalog=catalog,
            budget=_budget(celery_ram_budget_mb=16000, celery_cpu_budget=8, gpu=gpu),
            settings=_settings(min_concurrency=1, max_concurrency=16),
            depths={},
            history={},
            pending_sec=0,
        )
        self.assertEqual(plan.concurrency, 1)

    def test_cpu_fallback_false_pauses_queue(self) -> None:
        catalog = FootprintCatalog(
            tasks=(),
            by_queue={
                'sample_q': TaskFootprint(
                    queue='sample_q',
                    pattern='',
                    task_class=CLASS_HEAVY,
                    ram_mb=256,
                    cpu_units=1.0,
                    gpu_required=True,
                    vram_mb=4096,
                    max_parallel=4,
                    cpu_fallback=False,
                    module='sample_mod',
                )
            },
        )
        plan = plan_worker(
            _worker(),
            all_queues=['sample_q'],
            catalog=catalog,
            budget=_budget(),
            settings=_settings(min_concurrency=1),
            depths={},
            history={},
            pending_sec=0,
        )
        self.assertEqual(plan.concurrency, 1)
        self.assertEqual(plan.pause_queues, ['sample_q'])
        self.assertEqual(plan.queue_limits.get('sample_q'), 0)

    def test_mixed_all_worker_does_not_use_min_max_parallel(self) -> None:
        catalog = FootprintCatalog(
            tasks=(),
            by_queue={
                'sample_q': TaskFootprint(
                    queue='sample_q',
                    pattern='',
                    task_class=CLASS_HEAVY,
                    ram_mb=256,
                    cpu_units=1.0,
                    gpu_required=False,
                    vram_mb=0,
                    max_parallel=1,
                    cpu_fallback=True,
                    module='sample_mod',
                )
            },
        )
        plan = plan_worker(
            _worker(queues='all'),
            all_queues=['default', 'sample_q'],
            catalog=catalog,
            budget=_budget(celery_ram_budget_mb=16000, celery_cpu_budget=8),
            settings=_settings(max_concurrency=16),
            depths={},
            history={},
            pending_sec=0,
        )
        self.assertGreater(plan.concurrency, 1)
        self.assertEqual(plan.prefetch_multiplier, 1)
        self.assertEqual(plan.reserve_light, 2)
        self.assertEqual(plan.non_light_cap, plan.concurrency - 2)
        self.assertEqual(plan.queue_classes.get('default'), CLASS_LIGHT)
        self.assertEqual(plan.queue_classes.get('sample_q'), CLASS_HEAVY)
        self.assertEqual(plan.queue_limits.get('sample_q'), 1)

    def test_single_heavy_queue_has_no_light_reserve(self) -> None:
        catalog = FootprintCatalog(
            tasks=(),
            by_queue={
                'sample_q': TaskFootprint(
                    queue='sample_q',
                    pattern='',
                    task_class=CLASS_HEAVY,
                    ram_mb=256,
                    cpu_units=1.0,
                    gpu_required=False,
                    vram_mb=0,
                    max_parallel=1,
                    cpu_fallback=True,
                    module='sample_mod',
                )
            },
        )
        plan = plan_worker(
            _worker(queues=['sample_q']),
            all_queues=['sample_q'],
            catalog=catalog,
            budget=_budget(celery_ram_budget_mb=16000, celery_cpu_budget=8),
            settings=_settings(max_concurrency=16),
            depths={},
            history={},
            pending_sec=0,
        )
        self.assertEqual(plan.reserve_light, 0)
        self.assertEqual(plan.non_light_cap, 0)
        self.assertGreater(plan.concurrency, 1)

    def test_vram_reserve_reduces_free(self) -> None:
        snap = GpuSnapshot(
            True,
            1,
            8192.0,
            8192.0,
            0.0,
            (GpuDevice(0, 'gpu-0', 8192.0, 8192.0, 0.0),),
        )
        reserved = apply_vram_reserve(snap, extra_unassigned_mb=4096)
        self.assertEqual(reserved.vram_free_mb, 4096.0)
        self.assertEqual(gpu_slots_for_need(reserved, 4096), 1)


class HysteresisTests(unittest.TestCase):
    def test_skip_same_and_small_delta(self) -> None:
        self.assertTrue(should_skip_change(4, 4, 0.2))
        self.assertTrue(should_skip_change(5, 6, 0.2))
        self.assertFalse(should_skip_change(2, 8, 0.2))
        self.assertFalse(should_skip_change(None, 3, 0.2))


class SettingsTests(unittest.TestCase):
    def test_default_mode_is_auto_when_key_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CELERY_BALANCE': ''}):
            settings = load_settings(Path(tmp))
        self.assertEqual(settings.mode, 'auto')

    def test_explicit_off_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CELERY_BALANCE': 'off'}):
            settings = load_settings(Path(tmp))
        self.assertEqual(settings.mode, 'off')


class FootprintLoaderTests(unittest.TestCase):
    def test_loads_yaml_and_defaults_missing_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module_dir = root / 'modules' / 'sample_mod'
            (module_dir / 'api').mkdir(parents=True)
            (module_dir / 'task_footprint.yaml').write_text(
                'module: sample_mod\n'
                'defaults:\n'
                '  class: medium\n'
                'tasks:\n'
                '  - queue: sample_mod\n'
                '    class: heavy\n'
                '    ram_mb: 2048\n'
                '    cpu_units: 2\n'
                '    max_parallel: 1\n',
                encoding='utf-8',
            )
            catalog = load_footprints(str(root))
            found = resolve_footprint('sample_mod', catalog)
            self.assertEqual(found.task_class, CLASS_HEAVY)
            self.assertEqual(found.ram_mb, 2048)
            self.assertEqual(found.max_parallel, 1)
            missing = resolve_footprint('other_queue', catalog)
            self.assertEqual(missing.task_class, 'medium')
            self.assertEqual(resolve_footprint('default', catalog).task_class, CLASS_LIGHT)
            self.assertTrue(found.cpu_fallback)

    def test_process_role_reserve_fields(self) -> None:
        from process_roles_loader import load_module_process_roles

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module_dir = root / 'modules' / 'sample_mod'
            (module_dir / 'api').mkdir(parents=True)
            (module_dir / 'process_roles.yaml').write_text(
                'module: sample_mod\n'
                'roles:\n'
                '  - id: sample_daemon\n'
                '    reserve_host_budget: true\n'
                '    reserve_vram_mb: 4096\n'
                '    process_names:\n'
                '      - sampled\n'
                '    when:\n'
                '      - cmdline_contains_any:\n'
                '          - virtual_env/packages/sampled\n',
                encoding='utf-8',
            )
            rules = load_module_process_roles(str(root))
            self.assertEqual(len(rules), 1)
            self.assertTrue(rules[0].reserve_host_budget)
            self.assertEqual(rules[0].reserve_vram_mb, 4096.0)

    def test_broken_yaml_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module_dir = root / 'modules' / 'sample_mod'
            (module_dir / 'api').mkdir(parents=True)
            (module_dir / 'task_footprint.yaml').write_text(':\n  - not: yaml: [', encoding='utf-8')
            catalog = load_footprints(str(root))
            self.assertEqual(catalog.tasks, ())


class OverlayTests(unittest.TestCase):
    def test_write_decision_does_not_touch_workers_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            yaml_path = root / 'celery_workers.yaml'
            yaml_path.write_text('workers:\n  all:\n    concurrency: 8\n', encoding='utf-8')
            original = yaml_path.read_text(encoding='utf-8')
            plan = WorkerPlan(
                name='all',
                hostname='all_worker',
                queues=['default'],
                pool='threads',
                yaml_concurrency=8,
                concurrency=3,
                prefetch_multiplier=1,
                autoscale_min=None,
                autoscale_max=None,
                gpu_required=False,
                reasons=['test'],
                queue_limits={'default': 3},
                queue_classes={'default': CLASS_LIGHT, 'sample_q': CLASS_HEAVY},
                reserve_light=2,
                non_light_cap=6,
            )
            write_decision(
                root,
                settings=_settings(mode='auto'),
                budget={},
                queues={},
                forecast={},
                workers=[plan],
                yaml_workers={},
            )
            self.assertEqual(yaml_path.read_text(encoding='utf-8'), original)
            data = load_decision(root)
            self.assertIsNotNone(data)
            self.assertEqual(data['plans']['all']['concurrency'], 3)
            self.assertIsNone(worker_override(root, 'all', mode='recommend'))
            override = worker_override(root, 'all', mode='auto')
            self.assertIsNotNone(override)
            self.assertEqual(override.concurrency, 3)
            self.assertEqual(data['queue_limits']['default'], 3)
            self.assertEqual(data['queue_classes']['default'], CLASS_LIGHT)
            self.assertEqual(data['reserve_light'], 2)
            self.assertEqual(data['non_light_cap'], 6)
            self.assertIsNone(worker_override(root, 'missing-worker', mode='auto'))


class CgroupBudgetTests(unittest.TestCase):
    def test_cgroup_memory_parses_bytes(self) -> None:
        from unittest.mock import patch

        from celery_balance import host_budget

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'memory.max'
            path.write_text('2147483648\n', encoding='utf-8')
            with patch.object(host_budget, '_CGROUP_MEMORY_MAX', path), patch.object(
                host_budget,
                '_CGROUP_MEMORY_V1',
                Path(tmp) / 'missing',
            ):
                self.assertEqual(host_budget._read_cgroup_memory_mb(), 2048.0)


if __name__ == '__main__':
    unittest.main()
