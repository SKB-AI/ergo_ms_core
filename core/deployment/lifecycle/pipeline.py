"""Запуск шагов развёртывания: по одному или параллельной группой."""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402

from lifecycle.context import DeploymentContext  # noqa: E402
from lifecycle.steps.base import DeploymentStep, StepResult  # noqa: E402


def format_step_duration(elapsed_sec: float) -> str:
    """Строка вида '4.15s' или '500ms'."""
    if elapsed_sec < 1:
        return f'{elapsed_sec * 1000:.0f}ms'
    return f'{elapsed_sec:.2f}s'


def _print_step_elapsed(step_name: str, elapsed_sec: float, *, ok: bool) -> None:
    level = 'ok' if ok else 'error'
    print(
        format_console(
            level,
            t(
                'pipeline_step_elapsed',
                name=step_name,
                duration=format_step_duration(elapsed_sec),
            ),
        ),
        file=sys.stderr if not ok else None,
    )


def _run_one_step(step: DeploymentStep, ctx: DeploymentContext) -> tuple[StepResult, float]:
    started = time.perf_counter()
    result = step.run(ctx)
    return result, time.perf_counter() - started


class ParallelStepGroup(DeploymentStep):
    """Независимые шаги в одном пуле потоков. Падает, если любой шаг вернул ошибку."""

    def __init__(self, *steps: DeploymentStep, name: str) -> None:
        self._steps = list(steps)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def should_run(self, ctx: DeploymentContext) -> bool:
        return any(step.should_run(ctx) for step in self._steps)

    def run(self, ctx: DeploymentContext) -> StepResult:
        runnable = [step for step in self._steps if step.should_run(ctx)]
        if not runnable:
            return StepResult()
        if len(runnable) == 1:
            result, elapsed = _run_one_step(runnable[0], ctx)
            _print_step_elapsed(runnable[0].name, elapsed, ok=result.ok)
            return result

        print(format_console('info', t('pipeline_parallel_start', name=self._name)))
        failed: StepResult | None = None
        with ThreadPoolExecutor(max_workers=len(runnable)) as pool:
            futures = {pool.submit(_run_one_step, step, ctx): step for step in runnable}
            for future in as_completed(futures):
                step = futures[future]
                result, elapsed = future.result()
                _print_step_elapsed(step.name, elapsed, ok=result.ok)
                if not result.ok and failed is None:
                    if result.message:
                        print(format_console('error', result.message), file=sys.stderr)
                    failed = result
        return failed if failed is not None else StepResult()


class DeploymentPipeline:
    def __init__(self, steps: list[DeploymentStep]) -> None:
        self._steps = list(steps)

    def run(self, ctx: DeploymentContext) -> int:
        main = [step for step in self._steps if not step.run_as_cleanup]
        cleanup = [step for step in self._steps if step.run_as_cleanup]
        exit_code = 0
        try:
            for step in main:
                if not step.should_run(ctx):
                    continue
                result, elapsed = _run_one_step(step, ctx)
                _print_step_elapsed(step.name, elapsed, ok=result.ok)
                if not result.ok:
                    if result.message:
                        print(format_console('error', result.message), file=sys.stderr)
                    exit_code = result.exit_code
                    break
        finally:
            for step in cleanup:
                if not step.should_run(ctx):
                    continue
                result, elapsed = _run_one_step(step, ctx)
                _print_step_elapsed(step.name, elapsed, ok=result.ok)
                if not result.ok:
                    if result.message:
                        print(format_console('error', result.message), file=sys.stderr)
                    # Ошибка cleanup не затирает код основного сбоя; при успехе основного — пробрасываем.
                    if exit_code == 0:
                        exit_code = result.exit_code
        return exit_code
