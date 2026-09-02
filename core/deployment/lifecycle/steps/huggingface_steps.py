"""Шаг setup-full: снимки из modules/*/huggingface_models.yaml."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[2]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from lifecycle.context import DeploymentContext  # noqa: E402
from lifecycle.host import ops as host_ops  # noqa: E402
from lifecycle.steps.base import DeploymentStep, StepResult  # noqa: E402


class PullHuggingfaceModelsStep(DeploymentStep):
    """Качает снимки hook huggingface_models.yaml в virtual_env/trained_models/huggingface/."""

    @property
    def name(self) -> str:
        return 'pull_huggingface_models'

    def should_run(self, ctx: DeploymentContext) -> bool:
        return ctx.runtime == 'host'

    def run(self, ctx: DeploymentContext) -> StepResult:
        if not host_ops.venv_exists(ctx.project_root, ctx.platform):
            venv_py = host_ops.venv_python_exe(ctx.project_root, ctx.platform)
            print(format_console('error', t('venv_not_found_at', path=venv_py)), file=sys.stderr)
            return StepResult(exit_code=1, message=t('venv_not_found_msg'))

        venv_py = host_ops.venv_python_exe(ctx.project_root, ctx.platform)
        cli = _DEPLOYMENT_DIR / 'huggingface' / 'cli.py'
        print(format_console('info', t('hf_models_pulling')))
        code = subprocess.call(
            [str(venv_py), str(cli), 'install', '--root', str(ctx.project_root)],
            cwd=str(ctx.project_root),
            env=host_ops.api_env(ctx),
        )
        if code != 0:
            return StepResult(exit_code=code, message=t('hf_models_failed'))
        return StepResult()
