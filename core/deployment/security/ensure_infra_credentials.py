"""
Уникальные логин/пароль portable Postgres и Redis в databases.yaml.

Только пустые и шаблонные значения. Postgres — пока кластер ещё не создан.
Redis — пока в redis.conf нет requirepass (файл conf без AUTH не замораживает yaml).
Существующие не трогает, значения в лог не печатает. Stdlib — setup-full до venv.
При ERGO_BROKER=redis дописывает отсутствующую секцию redis, не затирая default.
"""

from __future__ import annotations

import re
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from ergo_modes import effective_redis_enabled, should_install_portable_postgres  # noqa: E402
from security.ensure_secret import (  # noqa: E402
    ACTION_ALREADY_SET,
    ACTION_ENV_MISSING,
    ACTION_GENERATED,
    ACTION_SKIPPED_MODE,
    ACTION_WRITE_FAILED,
    EnsureSecretAction,
    generate_secret_hex,
    secret_value_is_empty,
)

DATABASES_REL = 'databases.yaml'

TEMPLATE_POSTGRES_USERS = frozenset({'postgres', 'admin'})
TEMPLATE_REDIS_USERS = frozenset({'default', 'redis', 'admin'})
TEMPLATE_PASSWORDS = frozenset({'admin', 'postgres', 'changeme', 'password'})

_ADMIN_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{2,31}$')


def generate_admin_name(prefix: str = 'ergo') -> str:
    stem = prefix.strip().lower() or 'ergo'
    name = f'{stem}_{secrets.token_hex(4)}'
    if not _ADMIN_NAME_RE.fullmatch(name):
        name = f'ergo_{secrets.token_hex(4)}'
    return name


def _normalize(raw: str | None) -> str:
    return (raw or '').strip().strip('"').strip("'")


def _is_template_user(raw: str | None, templates: frozenset[str]) -> bool:
    value = _normalize(raw)
    return secret_value_is_empty(value) or value.lower() in templates


def _is_template_password(raw: str | None) -> bool:
    value = _normalize(raw)
    return secret_value_is_empty(value) or value.lower() in TEMPLATE_PASSWORDS


def _parse_simple_yaml_section(text: str, section: str) -> dict[str, str]:
    """Минимальный разбор секции databases.yaml без PyYAML."""
    lines = text.splitlines()
    in_databases = False
    in_section = False
    section_indent = -1
    result: dict[str, str] = {}
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        stripped = raw.strip()
        if stripped == 'databases:':
            in_databases = True
            in_section = False
            continue
        if not in_databases:
            continue
        if indent == 2 and stripped.endswith(':'):
            name = stripped[:-1].strip()
            in_section = name == section
            section_indent = indent
            continue
        if in_section and indent > section_indent and ':' in stripped:
            key, _, value = stripped.partition(':')
            value = value.strip().strip('"').strip("'")
            result[key.strip()] = value
        elif in_section and indent <= section_indent and stripped.endswith(':'):
            break
    return result


def _yaml_quote(value: str) -> str:
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _detect_newline(text: str) -> str:
    if '\r\n' in text:
        return '\r\n'
    return '\n'


_REDIS_SECTION_FALLBACK = (
    '  redis:\n'
    '    engine: "redis"\n'
    '    host: "127.0.0.1"\n'
    '    port: 6379\n'
    '    user: ""\n'
    '    password: ""\n'
    '    db_channel: 0\n'
    '    db_cache: 1\n'
    '    db_celery_broker: 2\n'
    '    db_celery_result: 3\n'
)


def databases_section_present(text: str, section: str) -> bool:
    """True, если под databases: есть секция с хотя бы одним ключом."""
    return bool(_parse_simple_yaml_section(text, section))


def ensure_redis_section_present(
    project_root: Path,
    yaml_path: Path,
    example_path: Path | None = None,
) -> bool:
    """
    Дописывает секцию redis из example, если её ещё нет.

    Не затирает default и прочие секции. Возвращает True, если файл изменился.
    """
    if not yaml_path.is_file():
        return False
    text = yaml_path.read_text(encoding='utf-8')
    if databases_section_present(text, 'redis'):
        return False
    example = example_path or (project_root / 'databases.yaml.example')
    block = ''
    if example.is_file():
        from config_scaffold.strategies import extract_databases_section_block

        block = extract_databases_section_block(example.read_text(encoding='utf-8'), 'redis')
    if not block.strip():
        block = _REDIS_SECTION_FALLBACK
    newline = _detect_newline(text)
    block = block.replace('\r\n', '\n').replace('\n', newline).rstrip() + newline
    yaml_path.write_text(text.rstrip() + newline + newline + block, encoding='utf-8')
    return True


def _postgres_cluster_exists(project_root: Path) -> bool:
    return (project_root / 'virtual_env' / 'packages' / 'postgres' / 'data' / 'PG_VERSION').is_file()


def _redis_conf_has_auth(project_root: Path) -> bool:
    """True, если portable redis.conf уже задаёт requirepass (не комментарий)."""
    conf = project_root / 'virtual_env' / 'packages' / 'redis' / 'conf' / 'redis.conf'
    if not conf.is_file():
        return False
    try:
        text = conf.read_text(encoding='utf-8')
    except OSError:
        return False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('requirepass '):
            return True
    return False


def upsert_yaml_section_key(path: Path, section: str, key: str, value: str) -> None:
    """Заменяет или вставляет ключ внутри секции databases.yaml без PyYAML."""
    text = path.read_text(encoding='utf-8')
    newline = _detect_newline(text)
    lines = text.splitlines()
    quoted = _yaml_quote(value)
    in_databases = False
    in_section = False
    section_indent = -1
    found = False
    out: list[str] = []

    def flush_missing() -> None:
        nonlocal found
        if in_section and not found:
            indent = ' ' * (section_indent + 2)
            out.append(f'{indent}{key}: {quoted}')
            found = True

    for raw in lines:
        indent = len(raw) - len(raw.lstrip(' '))
        stripped = raw.strip()

        if stripped == 'databases:':
            flush_missing()
            in_databases = True
            in_section = False
            out.append(raw)
            continue

        if in_databases and indent == 2 and stripped.endswith(':') and not stripped.startswith('#'):
            flush_missing()
            in_section = stripped[:-1].strip() == section
            section_indent = indent
            out.append(raw)
            continue

        if (
            in_section
            and indent > section_indent
            and not stripped.startswith('#')
            and stripped.startswith(f'{key}:')
        ):
            prefix = raw[:indent]
            out.append(f'{prefix}{key}: {quoted}')
            found = True
            continue

        if in_section and indent <= section_indent and stripped.endswith(':') and not stripped.startswith('#'):
            flush_missing()
            in_section = False

        out.append(raw)

    flush_missing()
    path.write_text(newline.join(out) + newline, encoding='utf-8')


def _ensure_password_field(
    *,
    yaml_path: Path,
    section: str,
    current: str,
    frozen: bool,
    replace_template_values: bool = True,
) -> EnsureSecretAction:
    should_fill = (
        _is_template_password(current)
        if replace_template_values
        else secret_value_is_empty(current)
    )
    if not should_fill:
        return ACTION_ALREADY_SET
    if frozen:
        return ACTION_ALREADY_SET
    upsert_yaml_section_key(yaml_path, section, 'password', generate_secret_hex())
    return ACTION_GENERATED


def _collect_section_credentials(
    section: str,
    data: Mapping[str, str],
) -> dict[tuple[str, str], str]:
    """Любые непустые user/password — при сбросе шаблона это уже заданные секреты."""
    preserved: dict[tuple[str, str], str] = {}
    user = data.get('user', '')
    password = data.get('password', '')
    if not secret_value_is_empty(password):
        preserved[(section, 'password')] = password
    if not secret_value_is_empty(user):
        preserved[(section, 'user')] = user
    return preserved


def _iter_databases_sections(text: str) -> tuple[str, ...]:
    names: list[str] = []
    in_databases = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        stripped = raw.strip()
        if stripped == 'databases:':
            in_databases = True
            continue
        if not in_databases:
            continue
        if indent == 2 and stripped.endswith(':'):
            names.append(stripped[:-1].strip())
    return tuple(names)


def _ensure_user_field(
    *,
    yaml_path: Path,
    section: str,
    current: str,
    templates: frozenset[str],
    frozen: bool,
    password_already_set: bool = False,
    replace_template_values: bool = True,
) -> EnsureSecretAction:
    already_set = (
        not _is_template_user(current, templates)
        if replace_template_values
        else not secret_value_is_empty(current)
    )
    if already_set:
        return ACTION_ALREADY_SET
    if frozen or password_already_set:
        return ACTION_ALREADY_SET
    upsert_yaml_section_key(yaml_path, section, 'user', generate_admin_name())
    return ACTION_GENERATED


def ensure_infra_credentials_locked(
    project_root: Path,
    values: Mapping[str, str],
    *,
    replace_template_values: bool = True,
) -> dict[str, tuple[EnsureSecretAction, str]]:
    """Вызывать под тем же lock, что ensure_mode_secrets. Не печатает значения.

    ``replace_template_values=False`` — только пустые поля (сброс из example):
    ``admin`` / ``postgres`` уже заданы человеком и не заменяются.
    """
    root = project_root.resolve()
    display = DATABASES_REL
    results: dict[str, tuple[EnsureSecretAction, str]] = {}
    yaml_path = root / DATABASES_REL

    def put(key: str, action: EnsureSecretAction) -> None:
        results[key] = (action, display)

    try:
        if should_install_portable_postgres(values):
            if not yaml_path.is_file():
                put('default.user', ACTION_ENV_MISSING)
                put('default.password', ACTION_ENV_MISSING)
            else:
                section = _parse_simple_yaml_section(
                    yaml_path.read_text(encoding='utf-8'),
                    'default',
                )
                frozen = _postgres_cluster_exists(root)
                current_password = section.get('password', '')
                password_already_set = (
                    not _is_template_password(current_password)
                    if replace_template_values
                    else not secret_value_is_empty(current_password)
                )
                put(
                    'default.user',
                    _ensure_user_field(
                        yaml_path=yaml_path,
                        section='default',
                        current=section.get('user', ''),
                        templates=TEMPLATE_POSTGRES_USERS,
                        frozen=frozen,
                        password_already_set=password_already_set,
                        replace_template_values=replace_template_values,
                    ),
                )
                section = _parse_simple_yaml_section(
                    yaml_path.read_text(encoding='utf-8'),
                    'default',
                )
                put(
                    'default.password',
                    _ensure_password_field(
                        yaml_path=yaml_path,
                        section='default',
                        current=section.get('password', ''),
                        frozen=frozen,
                        replace_template_values=replace_template_values,
                    ),
                )
        else:
            put('default.user', ACTION_SKIPPED_MODE)
            put('default.password', ACTION_SKIPPED_MODE)

        if effective_redis_enabled(values):
            if not yaml_path.is_file():
                put('redis.user', ACTION_ENV_MISSING)
                put('redis.password', ACTION_ENV_MISSING)
            else:
                ensure_redis_section_present(root, yaml_path)
                text = yaml_path.read_text(encoding='utf-8')
                section = _parse_simple_yaml_section(text, 'redis')
                if not section:
                    put('redis.user', ACTION_ENV_MISSING)
                    put('redis.password', ACTION_ENV_MISSING)
                else:
                    frozen = _redis_conf_has_auth(root)
                    current_password = section.get('password', '')
                    password_already_set = (
                        not _is_template_password(current_password)
                        if replace_template_values
                        else not secret_value_is_empty(current_password)
                    )
                    put(
                        'redis.user',
                        _ensure_user_field(
                            yaml_path=yaml_path,
                            section='redis',
                            current=section.get('user', ''),
                            templates=TEMPLATE_REDIS_USERS,
                            frozen=frozen,
                            password_already_set=password_already_set,
                            replace_template_values=replace_template_values,
                        ),
                    )
                    section = _parse_simple_yaml_section(
                        yaml_path.read_text(encoding='utf-8'),
                        'redis',
                    )
                    put(
                        'redis.password',
                        _ensure_password_field(
                            yaml_path=yaml_path,
                            section='redis',
                            current=section.get('password', ''),
                            frozen=frozen,
                            replace_template_values=replace_template_values,
                        ),
                    )
        else:
            put('redis.user', ACTION_SKIPPED_MODE)
            put('redis.password', ACTION_SKIPPED_MODE)
    except OSError:
        for key in ('default.user', 'default.password', 'redis.user', 'redis.password'):
            if key not in results or results[key][0] not in (ACTION_SKIPPED_MODE, ACTION_ALREADY_SET):
                put(key, ACTION_WRITE_FAILED)

    return results


def snapshot_live_credentials(yaml_path: Path) -> dict[tuple[str, str], str]:
    """Нешаблонные user/password из любой секции, независимо от кластера."""
    if not yaml_path.is_file():
        return {}
    text = yaml_path.read_text(encoding='utf-8')
    preserved: dict[tuple[str, str], str] = {}
    for section in _iter_databases_sections(text):
        data = _parse_simple_yaml_section(text, section)
        preserved.update(_collect_section_credentials(section, data))
    return preserved


def snapshot_secret_section_blocks(
    yaml_path: Path,
    skip: frozenset[str],
) -> dict[str, str]:
    """Секции с секретами, которые шаблон не копирует (celery, redis при local и т.п.)."""
    if not yaml_path.is_file():
        return {}
    text = yaml_path.read_text(encoding='utf-8')
    from config_scaffold.strategies import extract_databases_section_block

    blocks: dict[str, str] = {}
    for section in _iter_databases_sections(text):
        if section in skip:
            continue
        data = _parse_simple_yaml_section(text, section)
        if not _collect_section_credentials(section, data):
            continue
        block = extract_databases_section_block(text, section)
        if block.strip():
            blocks[section] = block
    return blocks


def restore_secret_section_blocks(
    yaml_path: Path,
    blocks: Mapping[str, str],
) -> tuple[str, ...]:
    """Дописывает сохранённые секции, если их нет после копирования шаблона."""
    if not blocks or not yaml_path.is_file():
        return ()
    text = yaml_path.read_text(encoding='utf-8')
    newline = _detect_newline(text)
    added: list[str] = []
    for section, block in blocks.items():
        if databases_section_present(text, section):
            continue
        normalized = block.replace('\r\n', '\n').replace('\n', newline).rstrip() + newline
        text = text.rstrip() + newline + newline + normalized
        added.append(section)
    if added:
        yaml_path.write_text(text, encoding='utf-8')
    return tuple(added)


def restore_live_credentials(
    yaml_path: Path,
    preserved: Mapping[tuple[str, str], str],
) -> tuple[str, ...]:
    """Возвращает имена секций, куда вернули сохранённые учётные данные."""
    if not preserved or not yaml_path.is_file():
        return ()
    restored: list[str] = []
    text = yaml_path.read_text(encoding='utf-8')
    for (section, key), value in preserved.items():
        current = _parse_simple_yaml_section(text, section)
        if not current:
            continue
        upsert_yaml_section_key(yaml_path, section, key, value)
        if section not in restored:
            restored.append(section)
        text = yaml_path.read_text(encoding='utf-8')
    return tuple(restored)


def ensure_infra_credentials(
    project_root: Path,
) -> dict[str, tuple[EnsureSecretAction, str]]:
    """Точка входа install-postgres / install-redis: тот же lock, что у секретов .env."""
    from env_file_loader import load_project_env
    from project_layout import env_secrets_lock_path
    from security.ensure_secret import _exclusive_lock

    root = project_root.resolve()
    try:
        with _exclusive_lock(env_secrets_lock_path(root)):
            values = dict(load_project_env(root))
            return ensure_infra_credentials_locked(root, values)
    except OSError:
        return {
            key: (ACTION_WRITE_FAILED, DATABASES_REL)
            for key in ('default.user', 'default.password', 'redis.user', 'redis.password')
        }
