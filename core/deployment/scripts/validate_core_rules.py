#!/usr/bin/env python3
"""
Проверка ядра ERGO MS на соответствие правилам Cursor / архитектуре.

Использование: ergoms core-rules-check

Включает:
- validate_module_isolation --scope=core --fail-on-warning (ядро)
- validate_module_isolation --scope=all (отчёт по modules/, без падения CI)
- validate_bridge_contracts --fail-on-warning (схемы дескрипторов моста)
- запрет hardcoded имён модулей во всём ядре и правилах Cursor
  (весь core/, .cursor/rules/ — код, комментарии, docstring, markdown)
- запрет console.error в прикладном коде клиента (кроме logError.js / logger.js)
- запрет нативного <select> / b-form-select в .vue ядра
- запрет from modules. в core/api/src (дополнительный текстовый grep)
- mid-indent / ast.parse для .py в core/api/src и core/deployment/scripts
- UTF-8 BOM в .ps1 deployment с не-ASCII (Windows PowerShell 5.1)
- LF без BOM в .sh deployment (Linux shebang и source)
- сырой ModelViewSet в core/api без ObjectPermissionMixin / BaseModelViewSet / admin-маркера
- README.md и AGENTS.md у каждого установленного модуля (есть api/apps.py или client/)
- контракт logout: один POST на волну, nginx избыток → 204 (не 429)
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
PROJECT_ROOT = _DEPLOYMENT_DIR.parent.parent

if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from validate_ps1_encoding import find_ps1_encoding_violations  # noqa: E402
from validate_sh_encoding import find_sh_encoding_violations  # noqa: E402
from validate_bridge_manifests import find_manifest_violations  # noqa: E402
from validate_data_boundaries import (  # noqa: E402
    find_cross_module_fk_violations,
    find_isolated_auth_fk_violations,
)
from security.checkers.object_permissions import find_raw_model_viewsets  # noqa: E402
from validate_logout_storm_guards import find_logout_storm_guard_violations  # noqa: E402
API_DIR = PROJECT_ROOT / 'core' / 'api'
CLIENT_SRC = PROJECT_ROOT / 'core' / 'client' / 'src'
CORE_DIR = PROJECT_ROOT / 'core'
CORE_CLIENT = PROJECT_ROOT / 'core' / 'client'
CORE_API_SRC = PROJECT_ROOT / 'core' / 'api' / 'src'
CORE_DEPLOYMENT = PROJECT_ROOT / 'core' / 'deployment'
CURSOR_RULES_DIR = PROJECT_ROOT / '.cursor' / 'rules'
MODULES_DIR = PROJECT_ROOT / 'modules'

HARDCODED_MODULE_SCAN_SUFFIXES = (
    '.py',
    '.js',
    '.vue',
    '.scss',
    '.ts',
    '.ps1',
    '.sh',
    '.cmd',
    '.md',
    '.mdc',
    '.yaml',
    '.yml',
    '.json',
    '.toml',
    '.conf',
    '.template',
    '.txt',
    '.html',
)

HARDCODED_MODULE_SKIP_DIR_NAMES = frozenset({
    'node_modules',
    'dist',
    '__pycache__',
    '.git',
    'coverage',
    '.vite',
    'cache',
})

CONSOLE_ERROR_ALLOWLIST = {
    CLIENT_SRC / 'js' / 'utils' / 'logError.js',
    CLIENT_SRC / 'js' / 'utils' / 'logger.js',
}

SELECT_PATTERNS = (
    re.compile(r'<\s*select\b', re.IGNORECASE),
    re.compile(r'<\s*b-form-select\b', re.IGNORECASE),
    re.compile(r'<\s*option\b', re.IGNORECASE),
)

MODULES_IMPORT_RE = re.compile(r'^\s*(from\s+modules\.|import\s+modules\.)')

HARDCODED_MODULE_ALLOWLIST_SUFFIXES = (
    'validate_core_rules.py',
    'validate_module_isolation.py',
    'validate_bridge_contracts.py',
    'validate_bridge_manifests.py',
    'validate_data_boundaries.py',
    'inventory_module_data.py',
    'module_deps.py',
    'sharedGlobs.generated.js',
)

HARDCODED_MODULE_ALLOWLIST_NAME_SUFFIXES = (
    '.generated.yml',
    '.generated.yaml',
    '.generated.js',
)

HARDCODED_MODULE_ALLOWLIST_REL = {
    'core/api/commands/base.py',
    'core/client/vite.config.js',
    'core/client/src/modules/core/ModuleLoader.js',
    'core/client/src/integrations/layoutPluginRegistry.js',
    'core/deployment/lifecycle/modules/module_source.py',
}

HARDCODED_MODULE_LINE_ALLOWLIST = (
    re.compile(r'modules/\*'),
    re.compile(r'modules/\$\{'),
    re.compile(r'@/modules/'),
    re.compile(r'\.\./.*modules/\*'),
    re.compile(r'modules\.'),
    re.compile(r'module_source\s*='),
    re.compile(r'MODULE_SOURCE\s*='),
    # Служебные ключи YAML/JSON/рецептов, совпадающие с именами модулей workers/tasks
    re.compile(r"\.get\(['\"](?:workers|tasks)['\"]"),
    re.compile(r"['\"]tasks['\"]:\s*(?:logging|\[)"),
    re.compile(
        r"['\"]workers['\"]:\s*['\"](?:install|start|stop|restart|status)-workers['\"]"
    ),
    re.compile(r"['\"]beat['\"],\s*['\"]workers['\"]"),
    re.compile(r'RootKey\s+["\']workers["\']'),
    re.compile(r'__pycache__'),
)

# Короткие/служебные токены: ловят Celery, vscode, generic-слова.
# Для них — только quoted literals; для остальных — ещё \bname\b (в т.ч. комментарии).
AMBIGUOUS_MODULE_NAME_TOKENS = frozenset({
    'crm',
    'lms',
    'projects',
    'students',
    'tasks',
    'workers',
})

# Официальный scaffold модулей — имя допустимо в правилах/доках ядра как эталон.
PLATFORM_MODULE_DOC_ALLOWLIST = frozenset({
    'module_template',
})

CLIENT_MODULE_LITERAL_ALLOWLIST = (
    'core/client/src/modules/core/ModuleLoader.js',
    'core/client/src/modules/core/sharedGlobs.generated.js',
)

CLIENT_CROSS_MODULE_IMPORT_RE = re.compile(
    r"""(?:^|\s)(?:import\s*\(|(?:import|from)\s+)['"]@/modules/([a-z][a-z0-9_-]*)/"""
)


def _python_executable() -> Path:
    if sys.platform == 'win32':
        candidate = PROJECT_ROOT / 'virtual_env' / 'python' / 'Scripts' / 'python.exe'
    else:
        candidate = PROJECT_ROOT / 'virtual_env' / 'python' / 'bin' / 'python'
    if candidate.is_file():
        return candidate
    return Path(sys.executable)


def run_module_isolation_check(*, scope: str, fail_on_error: bool) -> list[str]:
    """Запускает Django management command validate_module_isolation."""
    errors: list[str] = []
    python = _python_executable()
    cmd = [
        str(python),
        '-m',
        'commands',
        'validate_module_isolation',
        f'--scope={scope}',
        '--fail-on-warning',
    ]
    result = subprocess.run(
        cmd,
        cwd=str(API_DIR),
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'},
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    output = (result.stdout or '') + (result.stderr or '')
    if output.strip():
        print(output.rstrip())
    if result.returncode != 0 and fail_on_error:
        errors.append(t('core_rules_isolation_failed', scope=scope))
    return errors


def run_bridge_contracts_check() -> list[str]:
    """Запускает Django management command validate_bridge_contracts."""
    errors: list[str] = []
    python = _python_executable()
    cmd = [
        str(python),
        '-m',
        'commands',
        'validate_bridge_contracts',
        '--fail-on-warning',
    ]
    result = subprocess.run(
        cmd,
        cwd=str(API_DIR),
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'},
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    output = (result.stdout or '') + (result.stderr or '')
    if output.strip():
        print(output.rstrip())
    if result.returncode != 0:
        errors.append(t('core_rules_bridge_failed'))
    return errors


def _iter_files(root: Path, suffix: str) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob(f'*{suffix}'):
        if HARDCODED_MODULE_SKIP_DIR_NAMES.intersection(path.parts):
            continue
        files.append(path)
    return files


def _relative_posix(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _is_hardcoded_module_allowlisted(path: Path) -> bool:
    rel = _relative_posix(path)
    if rel in HARDCODED_MODULE_ALLOWLIST_REL:
        return True
    if any(part == 'migrations' for part in path.parts):
        return True
    if path.name in HARDCODED_MODULE_ALLOWLIST_SUFFIXES:
        return True
    name_lower = path.name.lower()
    if any(name_lower.endswith(suffix) for suffix in HARDCODED_MODULE_ALLOWLIST_NAME_SUFFIXES):
        return True
    return False


def _should_skip_hardcoded_module_path(rel: str) -> bool:
    """Пропуск тестов и lock-файлов — не runtime/доки ядра."""
    if rel.startswith('core/deployment/tests/') or '/lib/test/' in rel:
        return True
    if rel.endswith(('.lock', 'package-lock.json', 'poetry.lock')):
        return True
    return False


def load_installed_module_names() -> list[str]:
    if not MODULES_DIR.is_dir():
        return []
    names = []
    for entry in MODULES_DIR.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith('.') or entry.name == '__pycache__':
            continue
        if not re.fullmatch(r'[a-z][a-z0-9_]*', entry.name):
            continue
        names.append(entry.name)
    return sorted(names)


def check_hardcoded_module_names() -> list[str]:
    """Запрет имён установленных модулей во всём ядре и правилах Cursor.

    Область: весь ``core/`` и ``.cursor/rules/``.
    Quoted literals ('name' / "name") — для всех установленных модулей.
    Word-boundary name — для недвусмысленных имён (не AMBIGUOUS_MODULE_NAME_TOKENS).
    Комментарии (#, //, /* */, JSDoc *), docstring и markdown не исключаются.
    """
    module_names = load_installed_module_names()
    if not module_names:
        return []

    scannable_names = [
        name
        for name in module_names
        if name not in PLATFORM_MODULE_DOC_ALLOWLIST
    ]
    if not scannable_names:
        return []

    quoted_pattern = re.compile(
        r"(['\"])(" + "|".join(re.escape(name) for name in scannable_names) + r")\1"
    )
    distinctive = [
        name for name in scannable_names if name not in AMBIGUOUS_MODULE_NAME_TOKENS
    ]
    word_pattern = None
    if distinctive:
        word_pattern = re.compile(
            r'\b('
            + '|'.join(
                re.escape(name) for name in sorted(distinctive, key=len, reverse=True)
            )
            + r')\b'
        )
    scan_roots = [
        CORE_DIR,
        CURSOR_RULES_DIR,
    ]
    violations: list[str] = []
    seen: set[str] = set()

    for root in scan_roots:
        if not root.exists():
            continue
        if root.is_file():
            candidates = [root]
        else:
            candidates = []
            for suffix in HARDCODED_MODULE_SCAN_SUFFIXES:
                candidates.extend(_iter_files(root, suffix))

        for path in candidates:
            try:
                rel = _relative_posix(path)
            except ValueError:
                continue
            if rel in seen:
                continue
            seen.add(rel)
            if _is_hardcoded_module_allowlisted(path):
                continue
            if _should_skip_hardcoded_module_path(rel):
                continue
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                if any(pattern.search(line) for pattern in HARDCODED_MODULE_LINE_ALLOWLIST):
                    continue
                match = quoted_pattern.search(line)
                name = match.group(2) if match else None
                if name is None and word_pattern is not None:
                    word_match = word_pattern.search(line)
                    if word_match:
                        name = word_match.group(1)
                if name:
                    violations.append(
                        t(
                            'core_rules_hardcoded_module_name',
                            rel=rel,
                            line_no=line_no,
                            name=name,
                        )
                    )
    return violations


def _owner_module_from_client_path(path: Path) -> str | None:
    parts = path.parts
    try:
        idx = parts.index('modules')
        return parts[idx + 1]
    except (ValueError, IndexError):
        return None


def check_client_cross_module_imports() -> list[str]:
    """Запрет import из @/modules/<чужой модуль>/ в client коде модулей."""
    violations: list[str] = []
    if not MODULES_DIR.is_dir():
        return violations

    for mod_dir in MODULES_DIR.iterdir():
        client_root = mod_dir / 'client'
        if not client_root.is_dir():
            continue
        owner = mod_dir.name
        candidates: list[Path] = []
        for suffix in ('.js', '.vue', '.ts'):
            candidates.extend(_iter_files(client_root, suffix))

        for path in candidates:
            rel = _relative_posix(path)
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith('//') or stripped.startswith('*'):
                    continue
                for match in CLIENT_CROSS_MODULE_IMPORT_RE.finditer(line):
                    target = match.group(1)
                    if target == owner:
                        continue
                    violations.append(
                        t(
                            'core_rules_cross_module_import',
                            rel=rel,
                            line_no=line_no,
                            target=target,
                        )
                    )
    return violations


def check_client_hardcoded_module_paths() -> list[str]:
    """Запрет захардкоженных URL-путей модулей в ядре клиента.

    Ядро не должно знать маршруты установленных модулей: такие связи
    регистрируются модулем через ModuleBridge (session.scope_entry_routes,
    session_scope.module_context, layout.plugin_registry).
    """
    module_names = load_installed_module_names()
    if not module_names:
        return []

    path_pattern = re.compile(
        r"""['"`]/(""" + "|".join(re.escape(name) for name in module_names) + r""")(?=/|['"`])"""
    )
    violations: list[str] = []
    for suffix in ('.js', '.vue', '.ts'):
        for path in _iter_files(CLIENT_SRC, suffix):
            rel = _relative_posix(path)
            if rel in CLIENT_MODULE_LITERAL_ALLOWLIST:
                continue
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(lines, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                match = path_pattern.search(line)
                if match:
                    violations.append(
                        t(
                            'core_rules_hardcoded_module_route',
                            rel=rel,
                            line_no=line_no,
                            name=match.group(1),
                        )
                    )
    return violations


def check_client_module_literal_registry() -> list[str]:
    module_names = load_installed_module_names()
    if not module_names:
        return []

    pattern = re.compile(
        r"names\.add\(\s*(['\"])(" + "|".join(re.escape(name) for name in module_names) + r")\1\s*\)"
    )
    violations: list[str] = []
    for path in _iter_files(CLIENT_SRC, '.js'):
        rel = _relative_posix(path)
        if rel in CLIENT_MODULE_LITERAL_ALLOWLIST:
            continue
        if 'layoutPluginRegistry' in rel or 'ModuleLoader' in rel:
            continue
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(lines, start=1):
            match = pattern.search(line)
            if match:
                violations.append(
                    t(
                        'core_rules_names_add',
                        rel=rel,
                        line_no=line_no,
                        name=match.group(2),
                    )
                )
    return violations


def check_console_error() -> list[str]:
    violations: list[str] = []
    for path in _iter_files(CLIENT_SRC, '.js'):
        if path.suffix != '.js':
            continue
        if path.resolve() in {p.resolve() for p in CONSOLE_ERROR_ALLOWLIST}:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if 'console.error(' in line:
                rel = path.relative_to(PROJECT_ROOT)
                violations.append(
                    t('core_rules_console_error', rel=rel, line_no=line_no)
                )
    return violations


def check_native_select() -> list[str]:
    violations: list[str] = []
    for path in _iter_files(CLIENT_SRC, '.vue'):
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(p.search(line) for p in SELECT_PATTERNS):
                rel = path.relative_to(PROJECT_ROOT)
                violations.append(
                    t('core_rules_native_select', rel=rel, line_no=line_no)
                )
    return violations


def check_modules_imports_in_core() -> list[str]:
    violations: list[str] = []
    for path in _iter_files(CORE_API_SRC, '.py'):
        if 'validate_module_isolation' in path.name:
            continue
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if MODULES_IMPORT_RE.match(line):
                rel = path.relative_to(PROJECT_ROOT)
                violations.append(
                    t('core_rules_modules_import', rel=rel, line_no=line_no)
                )
    return violations


def _first_significant_line(source: str) -> str | None:
    """Первая непустая строка вне блочного docstring / комментариев в начале файла."""
    in_triple: str | None = None
    for raw in source.splitlines():
        line = raw.rstrip('\r\n')
        stripped = line.lstrip()
        if in_triple:
            if in_triple in stripped:
                in_triple = None
            continue
        if not stripped:
            continue
        if stripped.startswith('#'):
            continue
        if stripped.startswith(('"""', "'''")):
            quote = stripped[:3]
            rest = stripped[3:]
            if quote not in rest:
                in_triple = quote
            continue
        return line
    return None


def check_python_file_integrity() -> list[str]:
    """Запрет mid-function cut: mid-indent на старте файла и сломанный ast.parse."""
    violations: list[str] = []
    roots = (
        CORE_API_SRC,
        _DEPLOYMENT_DIR / 'scripts',
    )
    for root in roots:
        if not root.is_dir():
            continue
        for path in _iter_files(root, '.py'):
            rel = _relative_posix(path)
            try:
                source = path.read_text(encoding='utf-8-sig')
            except OSError as exc:
                violations.append(t('core_rules_py_read_failed', rel=rel, exc=exc))
                continue

            first = _first_significant_line(source)
            if first is not None and first[:1] in (' ', '\t'):
                violations.append(t('core_rules_py_mid_indent', rel=rel))

            try:
                ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                line = exc.lineno or '?'
                violations.append(
                    t('core_rules_py_syntax_error', rel=rel, line=line, msg=exc.msg)
                )
    return violations


def check_raw_model_viewsets() -> list[str]:
    """Сырой ModelViewSet ядра без object-scope или admin-маркера."""
    return [
        t('core_rules_raw_viewset', name=name)
        for name in find_raw_model_viewsets(CORE_API_SRC)
    ]


REQUIRED_MODULE_DOC_FILES = ('README.md', 'AGENTS.md')


def _module_dir_is_installed(entry: Path) -> bool:
    """Каталог установлен, если есть регистрация API или клиент."""
    if (entry / 'api' / 'apps.py').is_file():
        return True
    client = entry / 'client'
    return client.is_dir() and any(client.iterdir())


def check_module_docs() -> tuple[list[str], list[str]]:
    """README.md и AGENTS.md обязательны у установленного модуля.

    Пустой checkout (нет ``api/apps.py`` и нет ``client/``) — skip, не ошибка.
    Имена модулей в сообщениях берутся с диска, в исходнике их нет.
    """
    violations: list[str] = []
    skipped: list[str] = []
    if not MODULES_DIR.is_dir():
        return violations, skipped
    for entry in sorted(MODULES_DIR.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if entry.name.startswith('.') or entry.name == '__pycache__':
            continue
        rel_dir = f'modules/{entry.name}'
        if not _module_dir_is_installed(entry):
            skipped.append(rel_dir)
            continue
        for filename in REQUIRED_MODULE_DOC_FILES:
            if not (entry / filename).is_file():
                violations.append(
                    t('core_rules_module_docs_missing', rel=f'{rel_dir}/{filename}')
                )
    return violations, skipped


def main() -> int:
    all_errors: list[str] = []

    def _section(key: str) -> None:
        print()
        print(t(key))

    print(t('core_rules_heading_isolation_core'))
    all_errors.extend(run_module_isolation_check(scope='core', fail_on_error=True))

    _section('core_rules_heading_isolation_all')
    run_module_isolation_check(scope='all', fail_on_error=False)

    _section('core_rules_heading_bridge')
    all_errors.extend(run_bridge_contracts_check())

    _section('core_rules_heading_bridge_manifests')
    manifest_violations = find_manifest_violations()
    if manifest_violations:
        all_errors.extend(manifest_violations)
        for item in manifest_violations:
            print(format_console('error', t('core_rules_bridge_manifest_error', item=item)))
    else:
        print(format_console('ok', t('core_rules_bridge_manifests_ok')))

    _section('core_rules_heading_data_boundaries')
    boundary_violations = [
        msg for _rel, msg in (
            *find_cross_module_fk_violations(),
            *find_isolated_auth_fk_violations(),
        )
    ]
    if boundary_violations:
        all_errors.extend(boundary_violations)
        for item in boundary_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_data_boundaries_ok')))

    _section('core_rules_heading_hardcoded_names')
    hardcoded_violations = check_hardcoded_module_names()
    if hardcoded_violations:
        all_errors.extend(hardcoded_violations)
        for item in hardcoded_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_hardcoded_names_ok')))

    _section('core_rules_heading_hardcoded_routes')
    module_path_violations = check_client_hardcoded_module_paths()
    if module_path_violations:
        all_errors.extend(module_path_violations)
        for item in module_path_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_hardcoded_routes_ok')))

    _section('core_rules_heading_names_add')
    registry_violations = check_client_module_literal_registry()
    if registry_violations:
        all_errors.extend(registry_violations)
        for item in registry_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_names_add_ok')))

    _section('core_rules_heading_cross_module')
    cross_module_violations = check_client_cross_module_imports()
    if cross_module_violations:
        for item in cross_module_violations:
            print(format_console('warning', item))
        print(
            format_console(
                'info',
                t('core_rules_cross_module_info', count=len(cross_module_violations)),
            )
        )
    else:
        print(format_console('ok', t('core_rules_cross_module_ok')))

    _section('core_rules_heading_console_error')
    console_violations = check_console_error()
    if console_violations:
        all_errors.extend(console_violations)
        for item in console_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_console_error_ok')))

    _section('core_rules_heading_native_select')
    select_violations = check_native_select()
    if select_violations:
        all_errors.extend(select_violations)
        for item in select_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_native_select_ok')))

    _section('core_rules_heading_object_perms')
    raw_viewset_violations = check_raw_model_viewsets()
    if raw_viewset_violations:
        all_errors.extend(raw_viewset_violations)
        for item in raw_viewset_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_object_perms_ok')))

    _section('core_rules_heading_module_docs')
    docs_violations, docs_skipped = check_module_docs()
    for rel_dir in docs_skipped:
        print(format_console('skip', t('core_rules_module_docs_skip', rel=rel_dir)))
    if docs_violations:
        all_errors.extend(docs_violations)
        for item in docs_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_module_docs_ok')))

    _section('core_rules_heading_modules_import')
    import_violations = check_modules_imports_in_core()
    if import_violations:
        all_errors.extend(import_violations)
        for item in import_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_modules_import_ok')))

    _section('core_rules_heading_py_integrity')
    py_integrity_violations = check_python_file_integrity()
    if py_integrity_violations:
        all_errors.extend(py_integrity_violations)
        for item in py_integrity_violations:
            print(format_console('error', item))
    else:
        print(format_console('ok', t('core_rules_py_integrity_ok')))

    _section('core_rules_heading_ps1')
    ps1_violations = find_ps1_encoding_violations()
    if ps1_violations:
        for path in ps1_violations:
            rel = path.relative_to(PROJECT_ROOT)
            msg = t('core_rules_ps1_no_bom', rel=rel)
            all_errors.append(msg)
            print(format_console('error', msg))
    else:
        print(format_console('ok', t('core_rules_ps1_ok')))

    _section('core_rules_heading_sh')
    sh_violations = find_sh_encoding_violations()
    if sh_violations:
        for path, issues in sh_violations:
            rel = path.relative_to(PROJECT_ROOT)
            msg = t('core_rules_sh_issues', rel=rel, issues=', '.join(issues))
            all_errors.append(msg)
            print(format_console('error', msg))
    else:
        print(format_console('ok', t('core_rules_sh_ok')))

    _section('core_rules_heading_logout_storm')
    logout_storm_violations = find_logout_storm_guard_violations()
    if logout_storm_violations:
        for rel, marker in logout_storm_violations:
            msg = t('core_rules_logout_storm_missing', rel=rel, marker=marker)
            all_errors.append(msg)
            print(format_console('error', msg))
    else:
        print(format_console('ok', t('core_rules_logout_storm_ok')))

    if all_errors:
        print()
        print(format_console('error', t('core_rules_failed', count=len(all_errors))))
        return 1

    print()
    print(format_console('ok', t('core_rules_all_passed')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
