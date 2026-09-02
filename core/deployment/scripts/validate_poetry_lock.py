#!/usr/bin/env python3
"""
Проверяет, что poetry.lock не содержит пакетов модулей вне транзитивного дерева ядра.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from collections import deque
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DEPENDENCY_LINE_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*=\s*(.+)$"
)


def find_project_root() -> Path:
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent.parent.parent,
    ]
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise SystemExit("Не удалось найти корневой pyproject.toml.")


def normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def read_toml(path: Path) -> dict:
    with path.open("rb") as file_obj:
        return tomllib.load(file_obj)


def extract_poetry_dependencies(data: dict) -> set[str]:
    deps: set[str] = set()
    poetry = data.get("tool", {}).get("poetry", {})
    poetry_deps = poetry.get("dependencies", {})
    if isinstance(poetry_deps, dict):
        for dep_name in poetry_deps:
            normalized = normalize_name(dep_name)
            if normalized and normalized != "python":
                deps.add(normalized)

    groups = poetry.get("group", {})
    if isinstance(groups, dict):
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            group_deps = group.get("dependencies", {})
            if not isinstance(group_deps, dict):
                continue
            for dep_name in group_deps:
                normalized = normalize_name(dep_name)
                if normalized and normalized != "python":
                    deps.add(normalized)
    return deps


def collect_module_exclusive_dependencies(project_root: Path, root_deps: set[str]) -> set[str]:
    deployment = project_root / 'core' / 'deployment'
    if str(deployment) not in sys.path:
        sys.path.insert(0, str(deployment))
    from lifecycle.modules.catalog import ModuleCatalog  # noqa: WPS433

    catalog = ModuleCatalog.from_env(project_root)
    exclusive: set[str] = set()

    for module_dir in catalog.iter_module_dirs():
        pyproject_path = module_dir / "pyproject.toml"
        if not pyproject_path.exists():
            continue

        module_deps = extract_poetry_dependencies(read_toml(pyproject_path))
        for dep_name in module_deps:
            if dep_name not in root_deps:
                exclusive.add(dep_name)

    return exclusive


def parse_dependency_names(raw_value: str) -> list[str]:
    value = raw_value.strip()
    if not value:
        return []

    if value.startswith("{"):
        return []

    names: list[str] = []
    for part in value.split(","):
        token = part.strip().strip('"').strip("'")
        if not token:
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", token)
        if match:
            names.append(normalize_name(match.group(1)))
    return names


def parse_poetry_lock_graph(lock_path: Path) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    if not lock_path.exists():
        return graph

    current_name: str | None = None
    in_dependencies = False

    for line in lock_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()

        if stripped == "[[package]]":
            current_name = None
            in_dependencies = False
            continue

        if stripped.startswith("name = "):
            current_name = normalize_name(stripped.split("=", 1)[1].strip().strip('"'))
            graph.setdefault(current_name, set())
            in_dependencies = False
            continue

        if stripped == "[package.dependencies]":
            in_dependencies = True
            continue

        if stripped.startswith("[") and not stripped.startswith("[package.dependencies]"):
            in_dependencies = False
            continue

        if not in_dependencies or current_name is None:
            continue

        match = DEPENDENCY_LINE_RE.match(stripped)
        if not match:
            continue

        dep_name = normalize_name(match.group(1))
        graph[current_name].add(dep_name)

    return graph


def collect_reachable_packages(root_deps: set[str], graph: dict[str, set[str]]) -> set[str]:
    reachable: set[str] = set()
    queue = deque(root_deps)

    while queue:
        package_name = queue.popleft()
        if package_name in reachable:
            continue
        reachable.add(package_name)
        for dependency_name in graph.get(package_name, set()):
            if dependency_name not in reachable:
                queue.append(dependency_name)

    return reachable


def main() -> int:
    project_root = find_project_root()
    root_deps = extract_poetry_dependencies(read_toml(project_root / "pyproject.toml"))
    module_exclusive = collect_module_exclusive_dependencies(project_root, root_deps)
    graph = parse_poetry_lock_graph(project_root / "poetry.lock")
    reachable_from_root = collect_reachable_packages(root_deps, graph)

    leaked = sorted(
        package_name
        for package_name in module_exclusive
        if package_name in graph and package_name not in reachable_from_root
    )

    if leaked:
        print("[lock-check] poetry.lock содержит модульные пакеты вне дерева ядра:")
        for package_name in leaked:
            print(f"  - {package_name}")
        return 1

    print("[lock-check] poetry.lock: утечек модулей нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
