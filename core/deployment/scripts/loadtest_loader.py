"""
Загрузка сценариев нагрузки: core + modules/*/loadtest.yaml.

Discovery модулей — через ModuleCatalog (без импорта api модулей).
CLI: --root PATH --json [--targets core|all|name,...]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
_LOADTEST_DIR = _DEPLOYMENT_DIR / 'loadtest'
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import configure_stdio_utf8, format_console  # noqa: E402
from lifecycle.modules.catalog import ModuleCatalog  # noqa: E402

CORE_TARGET = 'core'
LOADTEST_FILENAME = 'loadtest.yaml'
CORE_SCENARIOS_FILE = _LOADTEST_DIR / 'core_scenarios.yaml'

_ALLOWED_METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'})
_ALLOWED_AUTH = frozenset({'bearer', 'none'})
_ALLOWED_PAGE_MODES = frozenset({'cold', 'warm'})
_ALLOWED_PROFILES = frozenset({'api', 'pages', 'mixed'})


@dataclass(frozen=True)
class LoadScenario:
    id: str
    method: str
    path: str
    weight: int
    auth: str
    expect_status: tuple[int, ...]
    target: str
    query: dict[str, str] = field(default_factory=dict)
    json: dict[str, Any] | None = None

    def to_runtime_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'id': self.id,
            'method': self.method,
            'path': self.path,
            'weight': self.weight,
            'auth': self.auth,
            'expect_status': list(self.expect_status),
            'target': self.target,
            'query': dict(self.query),
        }
        if self.json is not None:
            data['json'] = self.json
        return data


@dataclass(frozen=True)
class LoadPageRequest:
    id: str
    method: str
    path: str
    auth: str
    expect_status: tuple[int, ...]
    query: dict[str, str] = field(default_factory=dict)
    json: dict[str, Any] | None = None

    def to_runtime_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'id': self.id,
            'method': self.method,
            'path': self.path,
            'auth': self.auth,
            'expect_status': list(self.expect_status),
            'query': dict(self.query),
        }
        if self.json is not None:
            data['json'] = self.json
        return data


@dataclass(frozen=True)
class LoadPage:
    id: str
    weight: int
    mode: str
    parallel: bool
    target: str
    requests: tuple[LoadPageRequest, ...]

    def to_runtime_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'weight': self.weight,
            'mode': self.mode,
            'parallel': self.parallel,
            'target': self.target,
            'requests': [r.to_runtime_dict() for r in self.requests],
        }


@dataclass
class LoadTarget:
    name: str
    source: str
    enabled: bool
    tags: list[str]
    scenarios: list[LoadScenario]
    pages: list[LoadPage] = field(default_factory=list)


def _warn(message: str) -> None:
    print(format_console('warning', message), file=sys.stderr)


def _as_int_tuple(value: Any) -> tuple[int, ...]:
    if value is None:
        return (200,)
    if isinstance(value, (int, float)):
        return (int(value),)
    if isinstance(value, list):
        items: list[int] = []
        for item in value:
            try:
                items.append(int(item))
            except (TypeError, ValueError):
                continue
        return tuple(items) if items else (200,)
    return (200,)


def _as_query(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, raw in value.items():
        if key is None or raw is None:
            continue
        result[str(key)] = str(raw)
    return result


def _parse_json_body(
    raw: dict[str, Any],
    *,
    target: str,
    path: Path,
    scenario_id: str,
) -> tuple[bool, dict[str, Any] | None]:
    """
    Разобрать опциональный json body.
    Возвращает (ok, body): ok=False — невалидный json (пропуск сценария);
    body=None — ключ отсутствует (POST без тела).
    """
    if 'json' not in raw:
        return True, None
    value = raw.get('json')
    if not isinstance(value, dict):
        _warn(
            t(
                'loadtest_scenario_bad_json',
                path=path,
                target=target,
                scenario_id=scenario_id,
            )
        )
        return False, None
    return True, value


def _parse_http_fields(
    raw: dict[str, Any],
    *,
    target: str,
    path: Path,
    label: str,
) -> tuple[str, str, str, str, tuple[int, ...], dict[str, str], dict[str, Any] | None] | None:
    """id, method, path, auth, expect_status, query, json."""
    sid = str(raw.get('id') or '').strip()
    if not sid:
        _warn(t('loadtest_scenario_missing_id', path=path, target=target))
        return None

    method = str(raw.get('method') or 'GET').strip().upper()
    if method not in _ALLOWED_METHODS:
        _warn(
            t(
                'loadtest_scenario_bad_method',
                path=path,
                target=target,
                scenario_id=sid,
                method=method,
            )
        )
        return None

    req_path = str(raw.get('path') or '').strip()
    if not req_path:
        _warn(
            t(
                'loadtest_scenario_missing_path',
                path=path,
                target=target,
                scenario_id=sid,
            )
        )
        return None
    if not req_path.startswith('/'):
        req_path = f'/{req_path}'

    auth = str(raw.get('auth') or 'bearer').strip().lower()
    if auth not in _ALLOWED_AUTH:
        _warn(
            t(
                'loadtest_scenario_bad_auth',
                path=path,
                target=target,
                scenario_id=sid,
                auth=auth,
            )
        )
        return None

    json_ok, json_body = _parse_json_body(
        raw, target=target, path=path, scenario_id=sid
    )
    if not json_ok:
        return None

    return (
        sid,
        method,
        req_path,
        auth,
        _as_int_tuple(raw.get('expect_status')),
        _as_query(raw.get('query')),
        json_body,
    )


def _parse_scenario(
    raw: Any,
    *,
    target: str,
    path: Path,
) -> LoadScenario | None:
    if not isinstance(raw, dict):
        _warn(t('loadtest_scenario_must_be_object', path=path, target=target))
        return None

    parsed = _parse_http_fields(raw, target=target, path=path, label='scenario')
    if parsed is None:
        return None
    sid, method, req_path, auth, expect_status, query, json_body = parsed

    try:
        weight = max(1, int(raw.get('weight') or 1))
    except (TypeError, ValueError):
        weight = 1

    return LoadScenario(
        id=sid,
        method=method,
        path=req_path,
        weight=weight,
        auth=auth,
        expect_status=expect_status,
        target=target,
        query=query,
        json=json_body,
    )


def _parse_page_request(
    raw: Any,
    *,
    target: str,
    path: Path,
    page_id: str,
) -> LoadPageRequest | None:
    if not isinstance(raw, dict):
        _warn(
            t(
                'loadtest_page_request_must_be_object',
                path=path,
                target=target,
                page_id=page_id,
            )
        )
        return None
    parsed = _parse_http_fields(raw, target=target, path=path, label='page_request')
    if parsed is None:
        return None
    sid, method, req_path, auth, expect_status, query, json_body = parsed
    return LoadPageRequest(
        id=sid,
        method=method,
        path=req_path,
        auth=auth,
        expect_status=expect_status,
        query=query,
        json=json_body,
    )


def _parse_page(
    raw: Any,
    *,
    target: str,
    path: Path,
) -> LoadPage | None:
    if not isinstance(raw, dict):
        _warn(t('loadtest_page_must_be_object', path=path, target=target))
        return None

    page_id = str(raw.get('id') or '').strip()
    if not page_id:
        _warn(t('loadtest_page_missing_id', path=path, target=target))
        return None

    try:
        weight = max(1, int(raw.get('weight') or 1))
    except (TypeError, ValueError):
        weight = 1

    mode = str(raw.get('mode') or 'warm').strip().lower()
    if mode not in _ALLOWED_PAGE_MODES:
        _warn(
            t(
                'loadtest_page_bad_mode',
                path=path,
                target=target,
                page_id=page_id,
                mode=mode,
            )
        )
        return None

    parallel_raw = raw.get('parallel')
    parallel = True if parallel_raw is None else bool(parallel_raw)

    requests_raw = raw.get('requests')
    if not isinstance(requests_raw, list) or not requests_raw:
        _warn(
            t(
                'loadtest_page_requests_required',
                path=path,
                target=target,
                page_id=page_id,
            )
        )
        return None

    requests: list[LoadPageRequest] = []
    seen: set[str] = set()
    for item in requests_raw:
        req = _parse_page_request(item, target=target, path=path, page_id=page_id)
        if req is None:
            continue
        if req.id in seen:
            _warn(
                t(
                    'loadtest_page_request_duplicate_id',
                    path=path,
                    target=target,
                    page_id=page_id,
                    request_id=req.id,
                )
            )
            continue
        seen.add(req.id)
        requests.append(req)

    if not requests:
        _warn(
            t(
                'loadtest_page_no_valid_requests',
                path=path,
                target=target,
                page_id=page_id,
            )
        )
        return None

    return LoadPage(
        id=page_id,
        weight=weight,
        mode=mode,
        parallel=parallel,
        target=target,
        requests=tuple(requests),
    )


def _load_yaml_target(path: Path, *, target_name: str) -> LoadTarget | None:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        _warn(t('yaml_read_failed', path=path, exc=exc))
        return None

    if not text.strip():
        _warn(t('yaml_file_empty', path=path))
        return None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _warn(t('yaml_parse_error', path=path, exc=exc))
        return None

    if not isinstance(data, dict):
        _warn(t('yaml_root_must_be_object', path=path))
        return None

    enabled = bool(data.get('enabled', True))
    tags_raw = data.get('tags') or []
    tags: list[str] = []
    if isinstance(tags_raw, list):
        tags = [str(x).strip() for x in tags_raw if str(x).strip()]

    scenarios_raw = data.get('scenarios')
    pages_raw = data.get('pages')
    has_scenarios = isinstance(scenarios_raw, list) and bool(scenarios_raw)
    has_pages = isinstance(pages_raw, list) and bool(pages_raw)
    if not has_scenarios and not has_pages:
        _warn(t('loadtest_scenarios_or_pages_required', path=path))
        return None

    scenarios: list[LoadScenario] = []
    seen_scenario_ids: set[str] = set()
    if has_scenarios:
        for item in scenarios_raw:
            parsed = _parse_scenario(item, target=target_name, path=path)
            if parsed is None:
                continue
            if parsed.id in seen_scenario_ids:
                _warn(
                    t(
                        'loadtest_scenario_duplicate_id',
                        path=path,
                        target=target_name,
                        scenario_id=parsed.id,
                    )
                )
                continue
            seen_scenario_ids.add(parsed.id)
            scenarios.append(parsed)

    pages: list[LoadPage] = []
    seen_page_ids: set[str] = set()
    if has_pages:
        for item in pages_raw:
            parsed_page = _parse_page(item, target=target_name, path=path)
            if parsed_page is None:
                continue
            if parsed_page.id in seen_page_ids:
                _warn(
                    t(
                        'loadtest_page_duplicate_id',
                        path=path,
                        target=target_name,
                        page_id=parsed_page.id,
                    )
                )
                continue
            seen_page_ids.add(parsed_page.id)
            pages.append(parsed_page)

    if not scenarios and not pages:
        _warn(t('loadtest_no_valid_scenarios', path=path, target=target_name))
        return None

    return LoadTarget(
        name=target_name,
        source=str(path),
        enabled=enabled,
        tags=tags,
        scenarios=scenarios,
        pages=pages,
    )


def discover_targets(project_root: Path) -> list[LoadTarget]:
    """Все доступные цели: core + модули с loadtest.yaml."""
    targets: list[LoadTarget] = []

    if CORE_SCENARIOS_FILE.is_file():
        core = _load_yaml_target(CORE_SCENARIOS_FILE, target_name=CORE_TARGET)
        if core is not None:
            targets.append(core)
    else:
        _warn(t('loadtest_core_scenarios_missing', path=CORE_SCENARIOS_FILE))

    catalog = ModuleCatalog.from_env(project_root)
    for module_dir in catalog.iter_module_dirs():
        path = module_dir / LOADTEST_FILENAME
        if not path.is_file():
            continue
        loaded = _load_yaml_target(path, target_name=module_dir.name)
        if loaded is not None:
            targets.append(loaded)

    return targets


def parse_targets_arg(raw: str) -> set[str] | None:
    """
    None → специальное значение «all».
    Иначе множество имён целей (lowercase для сравнения — имена модулей case-sensitive,
    сравниваем как есть после strip).
    """
    text = (raw or '').strip()
    if not text:
        return {CORE_TARGET}
    if text.lower() == 'all':
        return None
    parts = [p.strip() for p in text.split(',') if p.strip()]
    return set(parts) if parts else {CORE_TARGET}


def parse_profile_arg(raw: str) -> str:
    profile = (raw or 'mixed').strip().lower()
    if profile not in _ALLOWED_PROFILES:
        raise ValueError(profile)
    return profile


def select_targets(
    all_targets: list[LoadTarget],
    selection: set[str] | None,
) -> list[LoadTarget]:
    """selection=None → все enabled; иначе фильтр по имени."""
    enabled = [item for item in all_targets if item.enabled]
    if selection is None:
        return enabled

    by_name = {item.name: item for item in enabled}
    chosen: list[LoadTarget] = []
    missing: list[str] = []
    for name in selection:
        hit = by_name.get(name)
        if hit is None:
            missing.append(name)
            continue
        chosen.append(hit)
    for name in missing:
        _warn(t('loadtest_target_not_found', name=name))
    order = {item.name: i for i, item in enumerate(enabled)}
    chosen.sort(key=lambda item: order.get(item.name, 999))
    return chosen


def collect_scenarios(targets: list[LoadTarget]) -> list[LoadScenario]:
    scenarios: list[LoadScenario] = []
    for target in targets:
        scenarios.extend(target.scenarios)
    return scenarios


def collect_pages(targets: list[LoadTarget]) -> list[LoadPage]:
    pages: list[LoadPage] = []
    for target in targets:
        pages.extend(target.pages)
    return pages


def apply_profile(
    scenarios: list[LoadScenario],
    pages: list[LoadPage],
    profile: str,
) -> tuple[list[LoadScenario], list[LoadPage]]:
    if profile == 'api':
        return scenarios, []
    if profile == 'pages':
        return [], pages
    return scenarios, pages


def needs_bearer_auth(
    scenarios: list[LoadScenario],
    pages: list[LoadPage],
) -> bool:
    if any(s.auth == 'bearer' for s in scenarios):
        return True
    for page in pages:
        if any(r.auth == 'bearer' for r in page.requests):
            return True
        # warm pages требуют bootstrap (bearer)
        if page.mode == 'warm':
            return True
    return False


def targets_payload(targets: list[LoadTarget]) -> dict[str, Any]:
    return {
        'targets': [
            {
                'name': item.name,
                'source': item.source,
                'enabled': item.enabled,
                'tags': item.tags,
                'scenario_count': len(item.scenarios),
                'page_count': len(item.pages),
                'scenarios': [asdict(s) for s in item.scenarios],
                'pages': [p.to_runtime_dict() for p in item.pages],
            }
            for item in targets
        ]
    }


def main(argv: list[str] | None = None) -> int:
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(description=t('loadtest_loader_description'))
    parser.add_argument('--root', type=Path, default=None, help=t('help_root_path'))
    parser.add_argument('--json', action='store_true', help=t('help_json_stdout'))
    parser.add_argument(
        '--targets',
        default='core',
        help=t('loadtest_help_targets'),
    )
    args = parser.parse_args(argv)

    root = args.root
    if root is None:
        root = _DEPLOYMENT_DIR.parent.parent
    root = root.resolve()
    if not (root / 'pyproject.toml').is_file():
        print(format_console('error', t('project_root_not_found', root=root)), file=sys.stderr)
        return 1

    all_targets = discover_targets(root)
    selection = parse_targets_arg(args.targets)
    selected = select_targets(all_targets, selection)

    if args.json:
        print(
            json.dumps(
                {
                    'available': targets_payload(all_targets)['targets'],
                    'selected': targets_payload(selected)['targets'],
                    'scenarios': [s.to_runtime_dict() for s in collect_scenarios(selected)],
                    'pages': [p.to_runtime_dict() for p in collect_pages(selected)],
                },
                ensure_ascii=False,
            )
        )
        return 0

    print(t('loadtest_available_targets'))
    for item in all_targets:
        flag = '' if item.enabled else ' [disabled]'
        print(
            f'  - {item.name} ({len(item.scenarios)} scenarios, '
            f'{len(item.pages)} pages){flag}'
        )
    print(t('loadtest_selected_targets'))
    if not selected:
        print(f'  {t("modules_none")}')
    else:
        for item in selected:
            print(f'  - {item.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
