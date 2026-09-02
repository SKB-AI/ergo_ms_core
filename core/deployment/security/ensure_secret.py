"""
Запись криптоключей в .env / env/*.env и учётных данных infra в databases.yaml,
если значение пусто.

Только пустые ключи, нужные текущим ERGO_*-режимам. Существующие
не трогает, значение в лог не печатает. Stdlib — setup-full до venv.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from env_file_loader import load_project_env, parse_env_file  # noqa: E402
from project_layout import env_secrets_lock_path  # noqa: E402
from ergo_modes import (  # noqa: E402
    effective_media_access_mode,
    effective_nginx_enabled,
    effective_search_enabled,
    env_bool,
    ergo_jupyter,
)

API_SECRET_KEY = 'API_SECRET_KEY'
API_JWT_SIGNING_KEY = 'API_JWT_SIGNING_KEY'
API_JUPYTER_TOKEN = 'API_JUPYTER_TOKEN'
MEDIA_API_INTERNAL_KEY = 'MEDIA_API_INTERNAL_KEY'
MEILI_MASTER_KEY = 'MEILI_MASTER_KEY'
BRIDGE_INTERNAL_TOKEN = 'BRIDGE_INTERNAL_TOKEN'

DEFAULT_SECRET_BYTES = 32

ACTION_GENERATED = 'generated'
ACTION_ALREADY_SET = 'already_set'
ACTION_ENV_MISSING = 'env_missing'
ACTION_WRITE_FAILED = 'write_failed'
ACTION_SKIPPED_MODE = 'skipped_mode'

EnsureSecretAction = Literal[
    'generated',
    'already_set',
    'env_missing',
    'write_failed',
    'skipped_mode',
]


@dataclass(frozen=True)
class SecretSpec:
    key: str
    rel_path: str
    needed: Callable[[Mapping[str, str]], bool]
    fallback_rel: str = ''


def generate_secret_hex(nbytes: int = DEFAULT_SECRET_BYTES) -> str:
    return secrets.token_hex(nbytes)


def secret_value_is_empty(raw: str | None) -> bool:
    return not (raw or '').strip().strip('"').strip("'")


def _always(_values: Mapping[str, str]) -> bool:
    return True


def _jupyter_token_needed(values: Mapping[str, str]) -> bool:
    explicit = (values.get('API_JUPYTER_ACCESS_MODE') or '').strip().lower()
    if explicit in ('lan', 'nginx'):
        return True
    if explicit == 'local':
        return False
    mode = ergo_jupyter(values)
    if mode in ('lan', 'nginx'):
        return True
    if mode == 'auto':
        if env_bool(values.get('API_JUPYTER_ALLOW_REMOTE')):
            return True
        if env_bool(values.get('API_JUPYTER_BEHIND_NGINX')) and effective_nginx_enabled(values):
            return True
        return False
    return False


def _media_internal_needed(values: Mapping[str, str]) -> bool:
    return effective_media_access_mode(values) == 'remote'


def _microservice_needed(values: Mapping[str, str]) -> bool:
    runtime = (values.get('MODULE_RUNTIME') or 'monolith').strip().lower()
    return runtime in ('microservice', 'split')


SECRET_SPECS: tuple[SecretSpec, ...] = (
    SecretSpec(API_SECRET_KEY, '.env', _always),
    SecretSpec(API_JWT_SIGNING_KEY, '.env', _always),
    SecretSpec(API_JUPYTER_TOKEN, 'env/jupyter.env', _jupyter_token_needed),
    SecretSpec(MEDIA_API_INTERNAL_KEY, 'env/media.env', _media_internal_needed),
    SecretSpec(MEILI_MASTER_KEY, 'env/search.env', effective_search_enabled, fallback_rel='.env'),
    SecretSpec(BRIDGE_INTERNAL_TOKEN, 'env/modules.env', _microservice_needed),
)


@contextmanager
def _exclusive_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt

            fh.seek(0)
            if fh.read(1) == b'':
                fh.write(b'0')
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == 'nt':
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _detect_newline(text: str) -> str:
    if '\r\n' in text:
        return '\r\n'
    return '\n'


def _assign_re(key: str) -> re.Pattern[str]:
    return re.compile(rf'^([ \t]*{re.escape(key)}[ \t]*=)(.*)$')


def _commented_assign_re(key: str) -> re.Pattern[str]:
    return re.compile(rf'^([ \t]*)#([ \t]*{re.escape(key)}[ \t]*=)(.*)$')


def _upsert_env_key(env_path: Path, key: str, value: str) -> None:
    text = env_path.read_text(encoding='utf-8')
    newline = _detect_newline(text)
    lines = text.splitlines()
    assign = _assign_re(key)
    commented = _commented_assign_re(key)
    found = False
    out: list[str] = []
    for line in lines:
        if not found:
            match = assign.match(line)
            if match and not line.lstrip().startswith('#'):
                out.append(f'{match.group(1)}{value}')
                found = True
                continue
            commented_match = commented.match(line)
            if commented_match:
                out.append(f'{key}={value}')
                found = True
                continue
        out.append(line)
    if not found:
        if out and out[-1].strip():
            out.append('')
        out.append(f'{key}={value}')
    env_path.write_text(newline.join(out) + newline, encoding='utf-8')


def _apply_to_environ(key: str, value: str) -> None:
    if secret_value_is_empty(os.environ.get(key)):
        os.environ[key] = value


def _resolve_target(project_root: Path, spec: SecretSpec) -> Path:
    preferred = project_root / spec.rel_path
    if preferred.is_file():
        return preferred
    if spec.fallback_rel:
        fallback = project_root / spec.fallback_rel
        if fallback.is_file():
            return fallback
    return preferred


def _file_value(env_path: Path, key: str) -> str:
    return parse_env_file(env_path).get(key, '')


def _ensure_one_locked(
    project_root: Path,
    spec: SecretSpec,
    values: Mapping[str, str],
) -> EnsureSecretAction:
    if not spec.needed(values):
        return ACTION_SKIPPED_MODE

    env_path = _resolve_target(project_root, spec)
    if not env_path.is_file():
        return ACTION_ENV_MISSING

    current = _file_value(env_path, spec.key)
    if not secret_value_is_empty(current):
        _apply_to_environ(spec.key, current.strip().strip('"').strip("'"))
        return ACTION_ALREADY_SET

    process_value = (os.environ.get(spec.key) or '').strip()
    if not secret_value_is_empty(process_value):
        return ACTION_ALREADY_SET

    secret = generate_secret_hex()
    if spec.key == MEDIA_API_INTERNAL_KEY:
        api_secret = (values.get(API_SECRET_KEY) or os.environ.get(API_SECRET_KEY) or '').strip()
        while api_secret and secret == api_secret:
            secret = generate_secret_hex()
    _upsert_env_key(env_path, spec.key, secret)
    os.environ[spec.key] = secret
    return ACTION_GENERATED


def ensure_mode_secrets(
    project_root: Path,
    *,
    replace_template_infra: bool = True,
) -> dict[str, tuple[EnsureSecretAction, str]]:
    """
    Заполняет пустые секреты, нужные текущим режимам.

    Возвращает key → (action, display_target). Не печатает значения.
    ``replace_template_infra=False`` — в databases.yaml не подменять
    уже заданные шаблонные пароли вроде admin (сброс из example).
    """
    root = project_root.resolve()
    lock_path = env_secrets_lock_path(root)
    results: dict[str, tuple[EnsureSecretAction, str]] = {}
    try:
        with _exclusive_lock(lock_path):
            values = dict(load_project_env(root))
            for spec in SECRET_SPECS:
                action = _ensure_one_locked(root, spec, values)
                target = _resolve_target(root, spec)
                display = target.relative_to(root).as_posix() if target.is_relative_to(root) else spec.rel_path
                results[spec.key] = (action, display)
                if action == ACTION_GENERATED:
                    values[spec.key] = os.environ.get(spec.key, '')
            from security.ensure_infra_credentials import ensure_infra_credentials_locked

            results.update(ensure_infra_credentials_locked(
                root,
                values,
                replace_template_values=replace_template_infra,
            ))
    except OSError:
        for spec in SECRET_SPECS:
            if spec.needed(load_project_env(root)):
                results[spec.key] = (ACTION_WRITE_FAILED, spec.rel_path)
        for key in ('default.user', 'default.password', 'redis.user', 'redis.password'):
            results[key] = (ACTION_WRITE_FAILED, 'databases.yaml')
    return results


def ensure_api_secret_key(project_root: Path) -> EnsureSecretAction:
    """Совместимость: только API_SECRET_KEY."""
    action, _target = ensure_mode_secrets(project_root).get(
        API_SECRET_KEY,
        (ACTION_SKIPPED_MODE, '.env'),
    )
    return action


def _announce_process(results: Mapping[str, tuple[EnsureSecretAction, str]]) -> None:
    for key, (action, target) in results.items():
        if action == ACTION_GENERATED:
            print(format_console('ok', t('secret_generated', key=key, target=target)))
        elif action == ACTION_ENV_MISSING:
            print(
                format_console('warning', t('secret_env_missing', key=key, target=target)),
                file=sys.stderr,
            )
        elif action == ACTION_WRITE_FAILED:
            print(
                format_console('error', t('secret_write_failed', key=key, target=target)),
                file=sys.stderr,
            )


def ensure_mode_secrets_for_process(project_root: Path) -> dict[str, tuple[EnsureSecretAction, str]]:
    """Точка входа скриптов запуска: записать пустые ключи текущих режимов."""
    results = ensure_mode_secrets(project_root)
    _announce_process(results)
    return results


def ensure_api_secret_for_process(project_root: Path) -> EnsureSecretAction:
    """Совместимость со старыми вызовами: все режимные секреты."""
    results = ensure_mode_secrets_for_process(project_root)
    action, _target = results.get(API_SECRET_KEY, (ACTION_SKIPPED_MODE, '.env'))
    return action
