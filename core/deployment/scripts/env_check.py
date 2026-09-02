#!/usr/bin/env python3
"""
Сверка .env с .env.example (корень, env/*.env, модули).

Используется: ergoms env
              ergoms env --reset-from-example [--yes]

--reset-from-example также заменяет databases.yaml и celery_workers.yaml.
Уже заданные ключи, пароли и токены в .env не затираются.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from config_scaffold import (  # noqa: E402
    ConfigScaffolder,
    ConfigTemplateRegistry,
    EnvCompareResult,
    ScaffoldAction,
    compare_env_files,
    parse_env_example_lines,
)
from console_tags import format_console  # noqa: E402
from security.ensure_secret import (  # noqa: E402
    ACTION_ENV_MISSING,
    ACTION_GENERATED,
    ACTION_WRITE_FAILED,
    ensure_mode_secrets,
)


def _resolve_project_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise SystemExit(
                format_console('error', t('env_check_root_missing', path=root)),
            )
        return root

    if (PROJECT_ROOT / 'pyproject.toml').is_file():
        return PROJECT_ROOT

    raise SystemExit(format_console('error', t('env_check_root_unknown')))


def _display_label(label: str) -> str:
    if label == '.env':
        return t('env_check_label_root')
    return label


def _format_result(
    result: EnvCompareResult,
    *,
    show_example_values: bool,
) -> list[str]:
    lines: list[str] = []
    lines.append(f'=== {_display_label(result.label)} ===')
    lines.append(t('env_check_env_path', path=result.env_path))
    lines.append(t('env_check_example_path', path=result.example_path))

    if result.errors:
        for error in result.errors:
            if error == 'example_missing':
                lines.append(format_console(
                    'error',
                    t('env_check_example_missing', path=result.example_path),
                ))
            elif error == 'env_missing':
                lines.append(format_console(
                    'error',
                    t('env_check_env_missing', path=result.env_path),
                ))
            else:
                lines.append(format_console('error', error))
        return lines

    lines.append(t(
        'env_check_counts',
        example_active=result.example_active,
        example_documented=result.example_documented,
        env_active=result.env_active,
    ))

    if result.missing:
        lines.append(t('env_check_missing_header', count=len(result.missing)))
        example_lines = (
            parse_env_example_lines(result.example_path)
            if show_example_values
            else {}
        )
        for key in result.missing:
            if show_example_values and key in example_lines:
                lines.append(f'  {example_lines[key]}')
            else:
                lines.append(f'  {key}')

    if result.extra:
        lines.append(t('env_check_extra_header', count=len(result.extra)))
        for key in result.extra:
            lines.append(f'  {key}')

    return lines


def run_check(
    project_root: Path,
    *,
    show_example_values: bool,
) -> int:
    pairs = ConfigTemplateRegistry.env_check_pairs(project_root)
    if not pairs:
        print(format_console('skip', t('env_check_no_pairs')))
        return 0

    total_missing = 0
    total_extra = 0
    has_errors = False
    checked = 0
    printed = 0

    for pair in pairs:
        result = compare_env_files(
            label=pair.label,
            example_path=pair.example_path,
            env_path=pair.env_path,
        )
        if result.example_exists and result.env_exists:
            checked += 1
        if result.has_errors:
            has_errors = True
        total_missing += len(result.missing)
        total_extra += len(result.extra)
        # Только пары с пропусками / лишними ключами / ошибками файлов
        if not result.has_diff:
            continue
        if printed:
            print()
        print('\n'.join(_format_result(
            result,
            show_example_values=show_example_values,
        )))
        printed += 1

    print()
    print(t(
        'env_check_summary',
        checked=checked,
        missing=total_missing,
        extra=total_extra,
        with_diff=printed,
    ))
    if has_errors or total_missing or total_extra:
        print(format_console('warning', t('env_check_has_diff')))
        return 1
    print(format_console('ok', t('env_check_ok')))
    return 0


def _confirm_reset(*, assume_yes: bool, targets: list[str]) -> bool:
    print(format_console('warning', t('env_reset_confirm_msg', count=len(targets))))
    for target in targets:
        print(f'  {target}')

    if assume_yes:
        return True

    if not sys.stdin.isatty():
        print(
            format_console('error', t('interactive_confirm_unavailable')),
            file=sys.stderr,
        )
        return False

    try:
        answer = input(t('continue_yn')).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        print(format_console('info', t('env_reset_cancelled')))
        return False

    if answer in ('y', 'yes'):
        return True

    print(format_console('info', t('env_reset_cancelled')))
    return False


def _print_secret_results(project_root: Path) -> int:
    write_failed = False
    for key, (action, target) in ensure_mode_secrets(
        project_root,
        replace_template_infra=False,
    ).items():
        if action == ACTION_GENERATED:
            print(format_console('ok', t('secret_generated', key=key, target=target)))
        elif action == ACTION_ENV_MISSING:
            print(format_console('warning', t('secret_env_missing', key=key, target=target)))
        elif action == ACTION_WRITE_FAILED:
            write_failed = True
            print(
                format_console('error', t('secret_write_failed', key=key, target=target)),
                file=sys.stderr,
            )
    return 1 if write_failed else 0


def run_reset(project_root: Path, *, assume_yes: bool) -> int:
    templates = ConfigTemplateRegistry.reset_templates(project_root)
    writable = [
        template
        for template in templates
        if (project_root / template.source_rel).is_file()
    ]
    if not writable:
        print(format_console('skip', t('env_reset_no_pairs')))
        return 0

    targets = [template.target_rel.replace('\\', '/') for template in writable]
    if not _confirm_reset(assume_yes=assume_yes, targets=targets):
        return 1

    results = ConfigScaffolder(project_root, templates=templates).run(overwrite=True)
    failed = False
    for result in results:
        target = result.display_target
        suffix = f' ({result.detail})' if result.detail else ''
        if result.action is ScaffoldAction.CREATED:
            print(format_console('ok', t('env_reset_created', target=target) + suffix))
        elif result.action is ScaffoldAction.OVERWRITTEN:
            print(format_console('ok', t('env_reset_overwritten', target=target) + suffix))
        elif result.action is ScaffoldAction.SKIPPED_NO_SOURCE:
            print(format_console(
                'warning',
                t('scaffold_example_missing', source_rel=result.source_rel),
            ))
        elif result.action is ScaffoldAction.FAILED:
            failed = True
            print(
                format_console(
                    'error',
                    t('scaffold_create_failed', target=target, detail=result.detail),
                ),
                file=sys.stderr,
            )

    if failed:
        return 1

    secret_status = _print_secret_results(project_root)
    if secret_status != 0:
        return secret_status

    print(format_console('ok', t('env_reset_done')))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=t('env_check_description'),
    )
    parser.add_argument('--root', help=t('env_check_help_root'))
    parser.add_argument(
        '--show-example-values',
        action='store_true',
        help=t('env_check_help_show_values'),
    )
    parser.add_argument(
        '--strict',
        action='store_true',
        help=t('env_check_help_strict'),
    )
    parser.add_argument(
        '--reset-from-example',
        action='store_true',
        help=t('env_reset_help'),
    )
    parser.add_argument(
        '-y',
        '--yes',
        action='store_true',
        help=t('env_reset_help_yes'),
    )
    args = parser.parse_args(argv)

    project_root = _resolve_project_root(args.root)
    if args.reset_from_example:
        return run_reset(project_root, assume_yes=args.yes)
    status = run_check(
        project_root,
        show_example_values=args.show_example_values,
    )
    if args.strict:
        return status
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
