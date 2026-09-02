"""
CLI снимков Hugging Face из modules/*/huggingface_models.yaml.

Вызов:
  python core/deployment/huggingface/cli.py list
  python core/deployment/huggingface/cli.py install [--force] [--repo org/name] [--include-optional]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HF_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _HF_DIR.parent
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from huggingface.registry import load_resolved_models  # noqa: E402
from huggingface.snapshot import adopt_legacy_snapshot  # noqa: E402
from huggingface.snapshot import install as install_snapshot  # noqa: E402
from huggingface.snapshot import is_installed  # noqa: E402


def _resolve_root(raw: Path | None) -> Path:
    root = (raw or _PROJECT_ROOT).resolve()
    if not (root / 'pyproject.toml').is_file():
        raise SystemExit(format_console('error', f'Корень проекта не найден: {root}'))
    return root


def cmd_list(root: Path) -> int:
    models = load_resolved_models(root)
    if not models:
        print(format_console('info', 'Нет записей в huggingface_models.yaml'))
        return 0
    print(format_console('info', f'Снимков Hugging Face: {len(models)}'))
    for model in models:
        mark = 'yes' if is_installed(root, model.repo_id) else 'no'
        required = '' if model.required else ' [optional]'
        print(f'  - {model.repo_id}  installed={mark}{required}  ← {model.source_module}')
    return 0


def cmd_install(
    root: Path,
    *,
    repo: str,
    force: bool,
    include_optional: bool,
) -> int:
    requested = (repo or '').strip()
    if requested:
        return install_snapshot(root, requested, force=force)

    models = load_resolved_models(root)
    if not models:
        print(format_console('skip', 'Нет моделей для установки (huggingface_models.yaml)'))
        return 0

    failed_required: list[str] = []
    for model in models:
        if not model.required and not include_optional:
            adopt_legacy_snapshot(root, model.repo_id)
            if is_installed(root, model.repo_id):
                install_snapshot(root, model.repo_id, force=False)
            else:
                print(
                    format_console(
                        'skip',
                        f'{model.repo_id} необязателен — setup-full не качает, '
                        'поставит первый вызов ensure_local_source',
                    )
                )
            continue
        code = install_snapshot(root, model.repo_id, force=force)
        if code == 0:
            continue
        if model.required:
            failed_required.append(model.repo_id)
        else:
            print(
                format_console(
                    'warning',
                    f'Не удалось скачать необязательную модель {model.repo_id}',
                )
            )

    if failed_required:
        names = ', '.join(failed_required)
        print(format_console('error', f'Не удалось скачать обязательные модели: {names}'))
        print(format_console('info', 'Повторите: ergoms pull-huggingface-models'))
        return 1

    print(format_console('ok', 'Снимки Hugging Face готовы'))
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--root', type=Path, default=_PROJECT_ROOT)
    parser = argparse.ArgumentParser(
        prog='ergoms pull-huggingface-models',
        description='Снимки Hugging Face из modules/*/huggingface_models.yaml',
        parents=[common],
    )
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='Список объявленных снимков', parents=[common])
    install_parser = sub.add_parser(
        'install',
        help='Скачать объявленные снимки',
        parents=[common],
    )
    install_parser.add_argument('--force', action='store_true')
    install_parser.add_argument(
        '--include-optional',
        action='store_true',
        help='Также поставить необязательные снимки (NMT и аналоги)',
    )
    install_parser.add_argument('--repo', default='', help='Только этот org/name')
    args = parser.parse_args(argv)
    root = _resolve_root(getattr(args, 'root', None))
    if args.command == 'list':
        return cmd_list(root)
    return cmd_install(
        root,
        repo=args.repo,
        force=args.force,
        include_optional=args.include_optional,
    )


if __name__ == '__main__':
    raise SystemExit(main())
