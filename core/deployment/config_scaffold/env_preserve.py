"""Сохранение уже заданных секретов при замене .env шаблоном."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from env_file_loader import parse_env_file  # noqa: E402
from security.ensure_secret import _upsert_env_key, secret_value_is_empty  # noqa: E402

# Имена, которые несут учётные данные, даже если в example есть значение по умолчанию.
_CREDENTIAL_KEYS = frozenset({
    'ADMIN_LOGIN',
    'ADMIN_PASSWORD',
    'EMAIL_HOST_USER',
    'EMAIL_HOST_PASSWORD',
    'FERNET_KEY',
})

# Хвост имени: криптоключ, пароль, токен. Не голое `_KEY` — это часто путь к файлу.
# Префикс `(?:^|_)` чтобы BYPASS не считался секретом из‑за хвоста PASS.
_SECRET_SUFFIX_RE = re.compile(
    r'(?:^|_)(?:SECRET_KEY|SIGNING_KEY|MASTER_KEY|API_KEY|INTERNAL_KEY|'
    r'INTERNAL_TOKEN|ACCESS_TOKEN|PASSWORD|SECRET|TOKEN|PASS|FERNET_KEY)$',
)

# Политика паролей, TTL токена, лимиты токенизатора — не секреты.
_NOT_SECRET_RE = re.compile(
    r'(?:_LIFETIME|_TTL(?:_|$)|_MAX_ATTEMPTS|_MIN_LENGTH|_MAX_LENGTH|'
    r'_ENABLED|_TOKENS|_TOKEN_SIGNAL|_TOKENIZERS_|_MAX_INPUT_TOKENS|'
    r'_MAX_NEW_TOKENS|_MAX_TOKENS|_REQUIRE_|_VALIDATE_|_KIND_)',
)

_CONFIG_VALUES = frozenset({
    'true', 'false', '1', '0', 'yes', 'no', 'on', 'off',
})


def parse_env_raw_values(path: Path) -> dict[str, str]:
    """KEY → правая часть KEY=… как в файле (кавычки не снимаются)."""
    if not path.is_file():
        return {}

    result: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, _, raw = stripped.partition('=')
        key = key.strip()
        if key:
            result[key] = raw
    return result


def is_preserved_secret_key(key: str) -> bool:
    """Имя похоже на секрет, а не на политику паролей или лимит токенов."""
    if key in _CREDENTIAL_KEYS:
        return True
    if _NOT_SECRET_RE.search(key):
        return False
    return bool(_SECRET_SUFFIX_RE.search(key))


def _looks_like_config_value(raw: str) -> bool:
    normalized = raw.strip().strip('"').strip("'").lower()
    if normalized in _CONFIG_VALUES:
        return True
    return normalized.isdigit() and len(normalized) < 8


def snapshot_env_secrets(env_path: Path, example_path: Path) -> dict[str, str]:
    """Непустые секреты рабочего файла, которые нельзя затирать шаблоном."""
    if not env_path.is_file():
        return {}

    live = parse_env_raw_values(env_path)
    example = parse_env_file(example_path) if example_path.is_file() else {}
    preserved: dict[str, str] = {}
    for key, raw in live.items():
        if secret_value_is_empty(raw):
            continue
        in_example = key in example
        example_empty = secret_value_is_empty(example.get(key, ''))
        if in_example and example_empty:
            preserved[key] = raw
            continue
        if is_preserved_secret_key(key) and not _looks_like_config_value(raw):
            preserved[key] = raw
    return preserved


def restore_env_secrets(env_path: Path, preserved: Mapping[str, str]) -> tuple[str, ...]:
    """Возвращает имена ключей, которые вернули в файл после копирования шаблона."""
    if not preserved or not env_path.is_file():
        return ()
    restored: list[str] = []
    for key, value in preserved.items():
        _upsert_env_key(env_path, key, value)
        restored.append(key)
    return tuple(restored)
