"""
Язык CLI ergoms: ERGO_CLI_LANGUAGE → системная локаль → ru.

Архитектура под N языков:
- Source of truth — русские строки в основных YAML (help.manifest.yaml, ergoms.help.yaml).
- Переводы — overlays: locales/<lang>/… (тот же каркас, строки на целевом языке).
- Сообщения CLI — locales/<lang>/cli_messages.yaml (и опционально cli_messages/*.yaml).
- Новый язык: каталог locales/<code>/ + overlays; код discovery без правки whitelist.

Модули: modules/<name>/locales/<lang>/ergoms.help.yaml
"""

from __future__ import annotations

import locale
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # portable Python до python-install / scaffold
    yaml = None  # type: ignore[assignment]

DEFAULT_CLI_LANGUAGE = 'ru'
ENV_CLI_LANGUAGE = 'ERGO_CLI_LANGUAGE'

# Зарезервировано для подсказок/доков; runtime принимает любой код с каталогом locales/<code>/.
BUNDLED_CLI_LANGUAGES: tuple[str, ...] = ('ru', 'en', 'fr')

_DEPLOYMENT_DIR = Path(__file__).resolve().parent
_LOCALES_DIR = _DEPLOYMENT_DIR / 'locales'

# Совместимость со старым импортом CliLang
CliLang = str


def normalize_cli_language(value: str | None) -> str | None:
    if not value:
        return None
    code = value.strip().lower().replace('-', '_')
    if not code:
        return None
    primary = code.split('_', 1)[0]
    if not primary.isalpha() or len(primary) < 2 or len(primary) > 8:
        return None
    return primary


@lru_cache(maxsize=1)
def available_cli_languages() -> tuple[str, ...]:
    """Языки с каталогом locales/<lang>/ (+ всегда ru)."""
    found: set[str] = {DEFAULT_CLI_LANGUAGE}
    if _LOCALES_DIR.is_dir():
        for child in _LOCALES_DIR.iterdir():
            if child.is_dir() and not child.name.startswith('.'):
                code = normalize_cli_language(child.name)
                if code:
                    found.add(code)
    return tuple(sorted(found))


def clear_locale_caches() -> None:
    available_cli_languages.cache_clear()
    _load_messages_catalog.cache_clear()
    _load_yaml_file.cache_clear()


def _system_locale_candidates() -> list[str]:
    candidates: list[str] = []
    for key in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
        raw = os.environ.get(key, '').strip()
        if raw:
            candidates.append(raw)
    try:
        loc = locale.getlocale()
        if loc and loc[0]:
            candidates.append(loc[0])
    except (ValueError, TypeError):
        pass
    if sys.platform == 'win32':
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            buf = ctypes.create_unicode_buffer(85)
            # LOCALE_NAME_USER_DEFAULT = 0
            if hasattr(kernel32, 'GetUserDefaultLocaleName'):
                if kernel32.GetUserDefaultLocaleName(buf, 85):
                    candidates.append(buf.value)
            else:
                lang_id = kernel32.GetUserDefaultUILanguage()
                primary = lang_id & 0xFF
                win_map = {0x19: 'ru', 0x09: 'en', 0x0C: 'fr'}
                mapped = win_map.get(primary)
                if mapped:
                    candidates.append(mapped)
        except Exception:
            pass
    return candidates


def resolve_cli_language(
    *,
    environ: dict[str, str] | None = None,
    project_root: Path | None = None,
) -> str:
    """
    Приоритет: ERGO_CLI_LANGUAGE (процесс / .env) → системная локаль → ru.
    DEFAULT_LANGUAGE не используется.
    Неизвестный код без locales/<code>/ → fallback ru.
    """
    env = environ if environ is not None else os.environ
    available = set(available_cli_languages())

    def _accept(code: str | None) -> str | None:
        if code and (code == DEFAULT_CLI_LANGUAGE or code in available):
            return code
        return None

    explicit = _accept(normalize_cli_language(env.get(ENV_CLI_LANGUAGE)))
    if explicit:
        return explicit

    if project_root is not None:
        try:
            from env_file_loader import load_project_env

            loaded = load_project_env(project_root)
            from_file = _accept(normalize_cli_language(loaded.get(ENV_CLI_LANGUAGE)))
            if from_file and not str(env.get(ENV_CLI_LANGUAGE, '')).strip():
                return from_file
        except Exception:
            pass

    for candidate in _system_locale_candidates():
        normalized = _accept(normalize_cli_language(candidate))
        if normalized:
            return normalized

    return DEFAULT_CLI_LANGUAGE


def deep_merge(base: Any, overlay: Any) -> Any:
    """Накладывает overlay на base: dict рекурсивно, list по индексу, иначе overlay."""
    if overlay is None:
        return base
    if isinstance(base, dict) and isinstance(overlay, dict):
        result = dict(base)
        for key, value in overlay.items():
            if key in result:
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    if isinstance(base, list) and isinstance(overlay, list):
        result = list(base)
        for index, value in enumerate(overlay):
            if index < len(result):
                result[index] = deep_merge(result[index], value)
            else:
                result.append(value)
        return result
    return overlay


_YAML_ESCAPE = {
    'n': '\n',
    't': '\t',
    'r': '\r',
    '\\': '\\',
    '"': '"',
    "'": "'",
}


def _unescape_double_quoted(value: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == '\\' and index + 1 < len(value):
            nxt = value[index + 1]
            out.append(_YAML_ESCAPE.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def _parse_flat_string_map_yaml(text: str) -> dict[str, str]:
    """
    Плоский ``key: "value"`` без PyYAML.

    Нужен для ``cli_messages`` на portable CPython до ``python-install``.
    Вложенные YAML (help.manifest и т.п.) этим парсером не читаются.
    """
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if ':' not in line:
            continue
        key, _, rest = line.partition(':')
        key = key.strip()
        if not key or any(char.isspace() for char in key):
            continue
        value = rest.strip()
        if not value:
            continue
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = _unescape_double_quoted(value[1:-1])
        elif len(value) >= 2 and value[0] == "'" and value[-1] == "'":
            value = value[1:-1]
        result[key] = value
    return result


@lru_cache(maxsize=64)
def _load_yaml_file(path_str: str) -> Any:
    """Полный YAML через PyYAML; без пакета — None (для вложенных манифестов)."""
    path = Path(path_str)
    if not path.is_file():
        return None
    if yaml is None:
        return None
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def load_localized_yaml(
    base_path: Path,
    lang: str | None = None,
    *,
    overlay_path: Path | None = None,
) -> Any:
    """
    Загружает base YAML и накладывает overlay для языка.
    overlay_path по умолчанию: рядом с base → locales/<lang>/<basename>
    или для module help: module/locales/<lang>/ergoms.help.yaml
    """
    language = lang or resolve_cli_language()
    base = _load_yaml_file(str(base_path.resolve()))
    if base is None:
        return None
    if language == DEFAULT_CLI_LANGUAGE:
        return base

    if overlay_path is None:
        overlay_path = base_path.parent / 'locales' / language / base_path.name
        if not overlay_path.is_file():
            # Ядро: core/deployment/help.manifest.yaml → locales/<lang>/help.manifest.yaml
            candidate = _LOCALES_DIR / language / base_path.name
            if candidate.is_file():
                overlay_path = candidate

    overlay = _load_yaml_file(str(overlay_path.resolve())) if overlay_path else None
    if overlay is None:
        return base
    return deep_merge(base, overlay)


def module_help_overlay_path(module_dir: Path, lang: str) -> Path:
    return module_dir / 'locales' / lang / 'ergoms.help.yaml'


def localize_value(value: Any, lang: str | None = None) -> str:
    """
    После перехода на overlays значения — обычные строки.
    Nested map {ru,en,fr} поддерживается как legacy fallback.
    """
    language = lang or resolve_cli_language()
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in (language, DEFAULT_CLI_LANGUAGE, 'en'):
            item = value.get(key)
            if isinstance(item, str) and item != '':
                return item
        for item in value.values():
            if isinstance(item, str) and item != '':
                return item
        return ''
    return str(value)


def localize_list(values: Any, lang: str | None = None) -> list[str]:
    if not values:
        return []
    language = lang or resolve_cli_language()
    return [localize_value(item, language) for item in values]


def _iter_message_yaml_files(lang: str) -> list[Path]:
    lang_dir = _LOCALES_DIR / lang
    files: list[Path] = []
    single = lang_dir / 'cli_messages.yaml'
    if single.is_file():
        files.append(single)
    multi_dir = lang_dir / 'cli_messages'
    if multi_dir.is_dir():
        files.extend(sorted(multi_dir.glob('*.yaml')))
        files.extend(sorted(multi_dir.glob('*.yml')))
    return files


@lru_cache(maxsize=16)
def _load_messages_catalog(lang: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in _iter_message_yaml_files(lang):
        text = path.read_text(encoding='utf-8')
        if yaml is not None:
            data = yaml.safe_load(text)
        else:
            data = _parse_flat_string_map_yaml(text)
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, str):
                result[key] = value
    return result


def clear_message_cache() -> None:
    clear_locale_caches()


def get_message_template(key: str, lang: str | None = None) -> str | None:
    language = lang or resolve_cli_language()
    template = _load_messages_catalog(language).get(key)
    if template is not None:
        return template
    if language != DEFAULT_CLI_LANGUAGE:
        return _load_messages_catalog(DEFAULT_CLI_LANGUAGE).get(key)
    return None


def t(message_key: str, *, lang: str | None = None, **params: Any) -> str:
    """Шаблон из locales/<lang>/cli_messages*.yaml; fallback ru; иначе сам ключ.

    Имя первого аргумента не ``key``: в шаблонах часто есть плейсхолдер ``{key}``,
    и ``t('…', key=…)`` иначе падает с TypeError.
    """
    template = get_message_template(message_key, lang)
    if template is None:
        template = message_key
    if params:
        try:
            return template.format(**params)
        except (KeyError, ValueError):
            return template
    return template


def ensure_project_env_loaded(project_root: Path | None) -> None:
    """Подтянуть .env в os.environ без перезаписи уже заданных ключей."""
    if project_root is None:
        return
    try:
        from env_file_loader import apply_project_env_to_environ

        apply_project_env_to_environ(project_root, override_existing=False)
    except Exception:
        pass
