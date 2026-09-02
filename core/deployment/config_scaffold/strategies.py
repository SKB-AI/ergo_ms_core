"""Стратегии копирования шаблонов конфигурационных файлов."""

from __future__ import annotations

import shutil
import sys
from abc import ABC, abstractmethod
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402


class CopyStrategy(ABC):
    """Интерфейс стратегии копирования example -> рабочий конфиг."""

    @abstractmethod
    def copy(self, source: Path, target: Path) -> None:
        ...

    def merge_into_existing(self, source: Path, target: Path) -> str:
        """Дописать недостающее в уже существующий файл. Пустая строка — без изменений."""
        return ''


class FullCopyStrategy(CopyStrategy):
    """Полное копирование файла."""

    def copy(self, source: Path, target: Path) -> None:
        shutil.copy2(source, target)


class EnvPreserveSecretsCopyStrategy(CopyStrategy):
    """Копирует .env.example и возвращает уже заданные ключи, пароли и токены."""

    def __init__(self) -> None:
        self.last_detail = ''

    def copy(self, source: Path, target: Path) -> None:
        from .env_preserve import restore_env_secrets, snapshot_env_secrets

        preserved = snapshot_env_secrets(target, source)
        shutil.copy2(source, target)
        restored = restore_env_secrets(target, preserved)
        if restored:
            self.last_detail = t('scaffold_env_secrets_kept', count=len(restored))
        else:
            self.last_detail = ''


class DatabasesYamlCopyStrategy(CopyStrategy):
    """
    Минимальный databases.yaml: всегда ``default``.

    Секция ``redis`` — при ERGO_BROKER=redis (или явном REDIS_ENABLED):
    в новый файл и в уже существующий, если секции ещё нет.
    Порт default: portable_postgres → 5433, postgres → 5432.
    Уже заданные user/password возвращаются после копирования шаблона,
    даже если portable-кластер ещё не создан. Секции с секретами вне
    минимального набора дописываются обратно.
    Решение читается в момент copy (после scaffold .env в том же прогоне).
    """

    _PORTABLE_PORT = 5433
    _SYSTEM_POSTGRES_PORT = 5432

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()
        self.last_detail = ''

    def copy(self, source: Path, target: Path) -> None:
        from security.ensure_infra_credentials import (
            restore_live_credentials,
            restore_secret_section_blocks,
            snapshot_live_credentials,
            snapshot_secret_section_blocks,
        )

        preserved = snapshot_live_credentials(target)
        values = self._env_values()
        sections = self._sections(values)
        extra_blocks = snapshot_secret_section_blocks(target, skip=frozenset(sections))
        NamedSectionsCopyStrategy(sections).copy(source, target)
        port = self._default_port_for_mode(values)
        if port is not None:
            self._rewrite_default_port(target, port)
        restored = restore_live_credentials(target, preserved)
        restored_extra = restore_secret_section_blocks(target, extra_blocks)

        bits = ['default']
        if 'redis' in sections:
            bits.append('redis')
        if port is not None:
            bits.append(f'port={port}')
        extra = ''
        if 'redis' not in sections:
            extra = t('scaffold_redis_hint')
        elif port is None:
            extra = t('scaffold_celery_hint')
        if restored or restored_extra:
            extra += t('scaffold_credentials_kept')
        self.last_detail = ', '.join(bits) + extra

    def merge_into_existing(self, source: Path, target: Path) -> str:
        from ergo_modes import effective_redis_enabled
        from security.ensure_infra_credentials import ensure_redis_section_present

        if not target.is_file() or not effective_redis_enabled(self._env_values()):
            return ''
        if ensure_redis_section_present(self._root, target, example_path=source):
            return t('scaffold_redis_section_added')
        return ''

    def _env_values(self) -> dict[str, str]:
        from env_file_loader import load_project_env, parse_env_file

        env_path = self._root / '.env'
        if env_path.is_file():
            return load_project_env(self._root)
        return parse_env_file(self._root / '.env.example')

    def _sections(self, values: dict[str, str]) -> tuple[str, ...]:
        from ergo_modes import effective_redis_enabled

        if effective_redis_enabled(values):
            return ('default', 'redis')
        return ('default',)

    def _default_port_for_mode(self, values: dict[str, str]) -> int | None:
        from ergo_modes import ergo_db

        mode = ergo_db(values)
        if mode == 'portable_postgres':
            return self._PORTABLE_PORT
        if mode == 'postgres':
            return self._SYSTEM_POSTGRES_PORT
        return None

    @staticmethod
    def _rewrite_default_port(target: Path, port: int) -> None:
        """Выставляет default.port без PyYAML."""
        lines = target.read_text(encoding='utf-8').splitlines()
        in_default = False
        replaced = False
        out: list[str] = []
        for raw in lines:
            indent = len(raw) - len(raw.lstrip(' '))
            stripped = raw.strip()
            if stripped == 'default:' and indent == 2:
                in_default = True
                out.append(raw)
                continue
            if in_default and indent == 2 and stripped.endswith(':') and not stripped.startswith('#'):
                in_default = False
            if in_default and indent > 2 and stripped.startswith('port:'):
                prefix = raw[: len(raw) - len(raw.lstrip(' '))]
                out.append(f'{prefix}port: {port}')
                replaced = True
                continue
            out.append(raw)
        if replaced:
            target.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')


class HeadLinesCopyStrategy(CopyStrategy):
    """Копирование только первых N строк (для простых усечений)."""

    def __init__(self, lines: int) -> None:
        self._lines = lines

    def copy(self, source: Path, target: Path) -> None:
        content = source.read_text(encoding='utf-8').splitlines()[: self._lines]
        text = '\n'.join(content)
        if text:
            text += '\n'
        target.write_text(text, encoding='utf-8')


class NamedSectionsCopyStrategy(CopyStrategy):
    """
    Копирует преамбулу и только указанные секции под ``databases:``.

    Без PyYAML (setup-full до venv). Порядок секций — как в ``sections``.
    Комментарии непосредственно перед секцией сохраняются вместе с ней.
    """

    def __init__(self, sections: tuple[str, ...]) -> None:
        if not sections:
            raise ValueError(t('named_sections_need_section'))
        self._sections = sections
        self._wanted = frozenset(sections)

    def copy(self, source: Path, target: Path) -> None:
        text = self._extract(source.read_text(encoding='utf-8'))
        target.write_text(text, encoding='utf-8')

    def _extract(self, text: str) -> str:
        lines = text.splitlines()
        databases_idx = self._find_databases_line(lines)
        if databases_idx is None:
            raise ValueError(t('template_missing_databases_key'))

        preamble = lines[:databases_idx]
        parsed = self._parse_sections(lines[databases_idx + 1 :])
        missing = [name for name in self._sections if name not in parsed]
        if missing:
            raise ValueError(
                t('template_missing_sections', sections=', '.join(missing)),
            )

        out: list[str] = list(preamble)
        if out and out[-1].strip():
            out.append('')
        out.append('databases:')
        for name in self._sections:
            pending, body = parsed[name]
            if out and out[-1].strip():
                out.append('')
            out.extend(pending)
            out.extend(body)

        return '\n'.join(out).rstrip() + '\n'

    @staticmethod
    def _find_databases_line(lines: list[str]) -> int | None:
        for idx, raw in enumerate(lines):
            if raw.strip() == 'databases:' and not raw.lstrip().startswith('#'):
                return idx
        return None

    def _parse_sections(
        self,
        after_databases: list[str],
    ) -> dict[str, tuple[list[str], list[str]]]:
        """Имя секции → (комментарии перед ней, строки секции включая заголовок)."""
        result: dict[str, tuple[list[str], list[str]]] = {}
        pending: list[str] = []
        current_name: str | None = None
        current_body: list[str] = []

        def commit_section() -> None:
            nonlocal current_name, current_body, pending
            if current_name is not None:
                result[current_name] = (list(pending), list(current_body))
                pending = []
            current_name = None
            current_body = []

        for raw in after_databases:
            indent = len(raw) - len(raw.lstrip(' '))
            stripped = raw.strip()
            is_section_header = (
                indent == 2
                and stripped.endswith(':')
                and not stripped.startswith('#')
                and ' ' not in stripped[:-1]
            )

            if is_section_header:
                commit_section()
                current_name = stripped[:-1].strip()
                current_body = [raw]
                continue

            if stripped == '':
                continue

            # Комментарии уровня секции — преамбула к следующей, не тело текущей.
            if stripped.startswith('#') and indent <= 2:
                if current_name is not None:
                    commit_section()
                pending.append(raw)
                continue

            if current_name is not None and indent > 2:
                current_body.append(raw)

        commit_section()
        return result


def extract_databases_section_block(text: str, section: str) -> str:
    """Комментарии перед секцией и её тело из databases.yaml.example, без ключа databases:."""
    parser = NamedSectionsCopyStrategy((section,))
    lines = text.splitlines()
    idx = parser._find_databases_line(lines)
    if idx is None:
        return ''
    parsed = parser._parse_sections(lines[idx + 1 :])
    pending_body = parsed.get(section)
    if not pending_body:
        return ''
    pending, body = pending_body
    parts = list(pending) + list(body)
    if not parts:
        return ''
    return '\n'.join(parts).rstrip() + '\n'
