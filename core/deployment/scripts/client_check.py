"""
Полная проверка клиента: lint, i18n, build, a11y.

Логи одного прогона: logs/client-check/<YYYYMMDD-HHMMSS>/
  01-lint.log … 04-a11y.log, summary.log, full.log
Указатель последнего прогона: logs/client-check/LATEST

Используется: ergoms client-check
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEPLOYMENT_DIR = SCRIPTS_DIR.parent
PROJECT_ROOT = DEPLOYMENT_DIR.parent.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from log_env import resolve_logs_dir  # noqa: E402
from project_layout import npm_exe, npm_root_dir, prepend_client_cli_path  # noqa: E402

KEEP_RUNS = 10
WORKSPACE = '@ergo-ms/core-client'

STEPS: list[tuple[str, str, list[str]]] = [
    ('01-lint', 'lint', ['run', 'lint:check', '-w', WORKSPACE]),
    ('02-i18n', 'i18n', ['run', 'check-i18n', '-w', WORKSPACE]),
    ('03-build', 'build', ['run', 'build', '-w', WORKSPACE]),
    ('04-a11y', 'a11y', ['run', 'lint:a11y', '-w', WORKSPACE]),
]


def _npm_env() -> tuple[str, Path, dict[str, str]]:
    npm_cmd = str(npm_exe(PROJECT_ROOT))
    if not Path(npm_cmd).is_file():
        npm_cmd = 'npm.cmd' if os.name == 'nt' else 'npm'

    npm_root = npm_root_dir(PROJECT_ROOT)
    env = os.environ.copy()
    prepend_client_cli_path(env, PROJECT_ROOT)
    return npm_cmd, npm_root, env


def _prune_old_runs(base: Path) -> None:
    runs = sorted(
        (p for p in base.iterdir() if p.is_dir() and p.name[:8].isdigit()),
        key=lambda p: p.name,
    )
    excess = len(runs) - KEEP_RUNS
    if excess <= 0:
        return
    for old in runs[:excess]:
        shutil.rmtree(old, ignore_errors=True)


def _run_step(
    *,
    index: int,
    total: int,
    step_id: str,
    title: str,
    npm_args: list[str],
    npm_cmd: str,
    npm_root: Path,
    env: dict[str, str],
    run_dir: Path,
    full_log: Path,
) -> int:
    banner = f'[{index}/{total}] {title} ({step_id})'
    step_log = run_dir / f'{step_id}.log'

    print(format_console('info', banner), flush=True)
    with full_log.open('a', encoding='utf-8') as full_fh:
        full_fh.write(f'\n===== {banner} =====\n')
        full_fh.flush()

    with step_log.open('w', encoding='utf-8') as step_fh:
        step_fh.write(f'{banner}\n')
        step_fh.write(f'cwd: {npm_root}\n')
        step_fh.write(f'command: {npm_cmd} {" ".join(npm_args)}\n\n')
        step_fh.flush()

        proc = subprocess.Popen(
            [npm_cmd, *npm_args],
            cwd=str(npm_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
        assert proc.stdout is not None
        with full_log.open('a', encoding='utf-8') as full_fh:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                step_fh.write(line)
                full_fh.write(line)
            code = proc.wait()
            footer = f'\n[exit] {code}\n'
            step_fh.write(footer)
            full_fh.write(footer)

    if code == 0:
        print(format_console('ok', t('client_check_ok', title=title)), flush=True)
    else:
        print(format_console('error', t('client_check_exit_code', title=title, code=code)), flush=True)
    return code


def main() -> int:
    configure_stdio_utf8()

    npm_root = npm_root_dir(PROJECT_ROOT)
    if not (npm_root / 'package.json').is_file():
        print(format_console('error', t('npm_workspace_not_found', npm_root=npm_root)), file=sys.stderr)
        return 1

    logs_root = resolve_logs_dir(PROJECT_ROOT)
    base = logs_root / 'client-check'
    base.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    run_dir = base / stamp
    run_dir.mkdir(parents=True, exist_ok=False)

    full_log = run_dir / 'full.log'
    summary_log = run_dir / 'summary.log'
    latest_ptr = base / 'LATEST'

    npm_cmd, npm_cwd, env = _npm_env()
    total = len(STEPS)
    results: list[tuple[str, str, int]] = []

    header = (
        f'client-check {stamp}\n'
        f'project: {PROJECT_ROOT}\n'
        f'run_dir: {run_dir}\n'
    )
    full_log.write_text(header, encoding='utf-8')
    print(format_console('info', t('client_check_run_dir', run_dir=run_dir)), flush=True)

    for index, (step_id, title, npm_args) in enumerate(STEPS, start=1):
        code = _run_step(
            index=index,
            total=total,
            step_id=step_id,
            title=title,
            npm_args=npm_args,
            npm_cmd=npm_cmd,
            npm_root=npm_cwd,
            env=env,
            run_dir=run_dir,
            full_log=full_log,
        )
        results.append((step_id, title, code))

    failed = [r for r in results if r[2] != 0]
    exit_code = 1 if failed else 0

    summary_lines = [
        f'client-check {stamp}',
        f'run_dir: {run_dir}',
        '',
        t('client_check_steps'),
    ]
    for step_id, title, code in results:
        status = 'OK' if code == 0 else f'FAIL ({code})'
        summary_lines.append(f'  {step_id} ({title}): {status}')
    summary_lines.append('')
    summary_lines.append(
        t(
            'client_check_summary',
            status='OK' if exit_code == 0 else 'FAIL',
            exit_code=exit_code,
        )
    )
    summary_text = '\n'.join(summary_lines) + '\n'
    summary_log.write_text(summary_text, encoding='utf-8')
    with full_log.open('a', encoding='utf-8') as full_fh:
        full_fh.write('\n===== summary =====\n')
        full_fh.write(summary_text)

    latest_ptr.write_text(f'{run_dir}\n', encoding='utf-8')
    _prune_old_runs(base)

    print(summary_text, end='')
    if exit_code == 0:
        print(format_console('ok', t('client_check_success', run_dir=run_dir)), flush=True)
    else:
        print(
            format_console(
                'error',
                t('client_check_failed', failed=len(failed), total=total, run_dir=run_dir),
            ),
            flush=True,
        )
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
