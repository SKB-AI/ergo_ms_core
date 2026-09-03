"""
Что собирает ``ergoms client-build`` на этом хосте.

Читает HOST_PROFILE, nginx-upstream'ы и списки модулей. Без Django и без имён модулей.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ergo_modes import effective_nginx_enabled
from lifecycle.host_profile import SERVICE_CLIENT, resolve_host_profile
from lifecycle.modules.catalog import parse_csv_modules, parse_disabled_modules_raw


def parse_client_module_remotes(raw: str = '') -> tuple[tuple[str, str], ...]:
    """``CLIENT_MODULE_REMOTES``: name=url,name2=url2."""
    items: list[tuple[str, str]] = []
    for part in (raw or '').split(','):
        token = part.strip()
        eq = token.find('=')
        if eq <= 0:
            continue
        name = token[:eq].strip()
        entry = token[eq + 1 :].strip()
        if name and entry:
            items.append((name, entry))
    return tuple(items)


def is_local_remote_entry(entry: str) -> bool:
    """Тот же origin: ``/remotes/<name>/…``. HTTP(S) — чужой хост."""
    value = (entry or '').strip()
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith('http://') or lowered.startswith('https://') or value.startswith('//'):
        return False
    return value.startswith('/remotes/')


def module_has_client(project_root: Path, module_name: str) -> bool:
    if not module_name:
        return False
    return (project_root / 'modules' / module_name / 'client').is_dir()


def _env_str(environ: Mapping[str, str], key: str) -> str:
    raw = environ.get(key, '')
    if raw is None:
        return ''
    return str(raw).strip()


def _has_upstream(environ: Mapping[str, str], key: str) -> bool:
    return bool(_env_str(environ, key))


@dataclass(frozen=True)
class ClientBuildPlan:
    shell: bool
    remotes: tuple[str, ...]

    def is_empty(self) -> bool:
        return not self.shell and not self.remotes


def resolve_client_build_plan(
    project_root: Path,
    environ: Mapping[str, str],
    *,
    only_modules: Sequence[str] | None = None,
) -> ClientBuildPlan:
    """Оболочка — если этот хост отдаёт местный ``core/client/dist``.

    Remotes — если этот хост отдаёт местный ``virtual_env/client-remotes``
    и на диске есть ``modules/<name>/client``.
    """
    disabled = parse_disabled_modules_raw(_env_str(environ, 'DISABLED_MODULES'))
    requested = tuple(
        name.strip()
        for name in (only_modules or ())
        if name and name.strip() and name.strip() not in disabled
    )
    if requested:
        return ClientBuildPlan(shell=False, remotes=requested)

    profile = resolve_host_profile(environ)
    shell = profile.wants(SERVICE_CLIENT) and not _has_upstream(environ, 'NGINX_CLIENT_UPSTREAM')

    if _has_upstream(environ, 'NGINX_CLIENT_REMOTES_UPSTREAM'):
        return ClientBuildPlan(shell=shell, remotes=())

    names: set[str] = set()
    for name in parse_csv_modules(_env_str(environ, 'MICROSERVICE_MODULES')):
        if name in disabled:
            continue
        if module_has_client(project_root, name):
            names.add(name)
    for name, entry in parse_client_module_remotes(_env_str(environ, 'CLIENT_MODULE_REMOTES')):
        if name in disabled or not is_local_remote_entry(entry):
            continue
        if module_has_client(project_root, name):
            names.add(name)
    return ClientBuildPlan(shell=shell, remotes=tuple(sorted(names)))


def should_reload_nginx_after_client_build(environ: Mapping[str, str]) -> bool:
    """После сборки пересобрать сайт-конфиг и reload, если этот хост отдаёт клиент через nginx."""
    return effective_nginx_enabled(environ)
