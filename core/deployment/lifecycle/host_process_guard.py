"""Отказ запускать процесс ядра на хосте, который его не обслуживает."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from cli_locale import t
from console_tags import format_console
from lifecycle.host_profile import resolve_host_profile, resolve_host_profile_from_root


def host_wants_service(
    service_id: str,
    *,
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    if project_root is not None:
        profile = resolve_host_profile_from_root(project_root, environ)
    else:
        profile = resolve_host_profile(environ or {})
    return profile.wants(service_id)


def refuse_unwanted_core_service(
    service_id: str,
    *,
    message_key: str,
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> int:
    """0 — служба разрешена; 1 — отказ с [ERROR] в stderr."""
    if host_wants_service(service_id, project_root=project_root, environ=environ):
        return 0
    print(format_console('error', t(message_key)), file=sys.stderr)
    return 1
