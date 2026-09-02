#!/usr/bin/env python3
"""Проверка зависимостей на известные уязвимости (OSV + npm audit)."""

from __future__ import annotations

import json
import os
import re
import ast
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
if str(DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYMENT_DIR))

from cli_locale import t  # noqa: E402
from console_tags import format_console  # noqa: E402
from project_layout import npm_root_dir, virtual_env_dir  # noqa: E402

OSV_QUERYBATCH = 'https://api.osv.dev/v1/querybatch'
OSV_VULN = 'https://api.osv.dev/v1/vulns/'
OSV_TIMEOUT_SEC = 60
FAIL_SEVERITIES = frozenset({'HIGH', 'CRITICAL'})
CVSS_FAIL_THRESHOLD = 7.0
LOCK_PACKAGE_RE = re.compile(r'^name\s*=\s*"(?P<name>[^"]+)"\s*$')
LOCK_VERSION_RE = re.compile(r'^version\s*=\s*"(?P<version>[^"]+)"\s*$')
LOCK_GROUPS_RE = re.compile(r'^groups\s*=\s*(?P<groups>.+)$')


def _project_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parents[3]]
    for candidate in candidates:
        if (candidate / 'pyproject.toml').is_file():
            return candidate
    raise SystemExit(format_console('error', t('deps_audit_root_missing')))


def _parse_poetry_lock(lock_path: Path) -> list[tuple[str, str]]:
    packages: list[tuple[str, str]] = []
    if not lock_path.is_file():
        return packages
    name = version = None
    groups: list[str] = ['main']
    in_package = False
    for raw in lock_path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line == '[[package]]':
            if in_package and name and version and 'main' in groups:
                packages.append((name, version))
            name = version = None
            groups = ['main']
            in_package = True
            continue
        if not in_package:
            continue
        match = LOCK_PACKAGE_RE.match(line)
        if match:
            name = match.group('name')
            continue
        match = LOCK_VERSION_RE.match(line)
        if match:
            version = match.group('version')
            continue
        match = LOCK_GROUPS_RE.match(line)
        if match:
            try:
                parsed = ast.literal_eval(match.group('groups').strip())
                groups = [str(item) for item in parsed] if isinstance(parsed, list) else ['main']
            except (SyntaxError, ValueError):
                groups = ['main']
    if in_package and name and version and 'main' in groups:
        packages.append((name, version))
    return packages


_CVSS3_AV = {'N': 0.85, 'A': 0.62, 'L': 0.55, 'P': 0.2}
_CVSS3_AC = {'L': 0.77, 'H': 0.44}
_CVSS3_PR_U = {'N': 0.85, 'L': 0.62, 'H': 0.27}
_CVSS3_PR_C = {'N': 0.85, 'L': 0.68, 'H': 0.5}
_CVSS3_UI = {'N': 0.85, 'R': 0.62}
_CVSS3_CIA = {'H': 0.56, 'L': 0.22, 'N': 0.0}
_LABEL_RANK = {'CRITICAL': 4, 'HIGH': 3, 'MODERATE': 2, 'MEDIUM': 2, 'LOW': 1, 'UNKNOWN': 0}


def _cvss_roundup(value: float) -> float:
    return int(value * 10 + (0 if abs(value * 10 - int(value * 10)) < 1e-9 else 1)) / 10


def _cvss_v3_base_score(vector: str) -> float:
    """Базовый балл CVSS 3.x из вектора OSV (`CVSS:3.1/AV:N/...`). 0 если разобрать нельзя."""
    text = (vector or '').strip()
    try:
        return float(text)
    except ValueError:
        pass
    metrics: dict[str, str] = {}
    for part in text.split('/'):
        if ':' not in part:
            continue
        key, value = part.split(':', 1)
        if key.upper() in {'CVSS', 'CVSS:3.0', 'CVSS:3.1'}:
            continue
        metrics[key.upper()] = value.upper()
    if 'AV' not in metrics or 'C' not in metrics:
        return 0.0
    scope = metrics.get('S', 'U')
    priv = _CVSS3_PR_C if scope == 'C' else _CVSS3_PR_U
    try:
        av = _CVSS3_AV[metrics['AV']]
        ac = _CVSS3_AC[metrics.get('AC', 'L')]
        pr = priv[metrics.get('PR', 'N')]
        ui = _CVSS3_UI[metrics.get('UI', 'N')]
        c_w = _CVSS3_CIA[metrics.get('C', 'N')]
        i_w = _CVSS3_CIA[metrics.get('I', 'N')]
        a_w = _CVSS3_CIA[metrics.get('A', 'N')]
    except KeyError:
        return 0.0
    isc = 1 - (1 - c_w) * (1 - i_w) * (1 - a_w)
    if isc <= 0:
        return 0.0
    if scope == 'C':
        impact = 7.52 * (isc - 0.029) - 3.25 * (isc - 0.02) ** 15
    else:
        impact = 6.42 * isc
    exploitability = 8.22 * av * ac * pr * ui
    if scope == 'C':
        raw = min(1.08 * (impact + exploitability), 10.0)
    else:
        raw = min(impact + exploitability, 10.0)
    return _cvss_roundup(raw)


def _severity_from_score(score: float) -> str:
    if score >= 9.0:
        return 'CRITICAL'
    if score >= CVSS_FAIL_THRESHOLD:
        return 'HIGH'
    if score >= 4.0:
        return 'MODERATE'
    if score > 0:
        return 'LOW'
    return 'UNKNOWN'


def _stronger_label(current: str, candidate: str) -> str:
    cand = 'MODERATE' if candidate == 'MEDIUM' else candidate
    cur = 'MODERATE' if current == 'MEDIUM' else current
    if _LABEL_RANK.get(cand, 0) > _LABEL_RANK.get(cur, 0):
        return cand
    return cur


def _osv_severity(vuln: dict) -> tuple[str, float]:
    label = 'UNKNOWN'
    score = 0.0
    for item in vuln.get('severity') or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get('score') or '').strip()
        kind = str(item.get('type') or '').upper()
        if kind in {'CVSS_V3', 'CVSS_V31'} or text.startswith('CVSS:3'):
            score = max(score, _cvss_v3_base_score(text))
        elif kind == 'CVSS_V4' or text.startswith('CVSS:4'):
            try:
                score = max(score, float(text))
            except ValueError:
                pass
        else:
            try:
                score = max(score, float(text))
            except ValueError:
                pass
    db = vuln.get('database_specific') or {}
    if isinstance(db, dict):
        raw = str(db.get('severity') or '').upper()
        if raw in FAIL_SEVERITIES or raw in {'MODERATE', 'MEDIUM', 'LOW'}:
            label = _stronger_label(label, raw)
        cvss = db.get('cvss')
        if isinstance(cvss, dict):
            try:
                score = max(score, float(cvss.get('score') or 0))
            except (TypeError, ValueError):
                pass
            cvss_label = str(cvss.get('severity') or '').upper()
            if cvss_label:
                label = _stronger_label(label, cvss_label)
    scored = _severity_from_score(score)
    label = _stronger_label(label, scored)
    return label, score


def _vuln_needs_hydrate(vuln: dict) -> bool:
    if vuln.get('severity'):
        return False
    db = vuln.get('database_specific')
    if isinstance(db, dict) and db.get('severity'):
        return False
    return True


def _hydrate_osv_vuln(vuln: dict, cache: dict[str, dict]) -> dict:
    """querybatch отдаёт id без CVSS — добираем карточку /v1/vulns/{id}."""
    if not isinstance(vuln, dict):
        return {}
    vuln_id = str(vuln.get('id') or '').strip()
    if not _vuln_needs_hydrate(vuln):
        return vuln
    if not vuln_id:
        return vuln
    cached = cache.get(vuln_id)
    if cached is not None:
        return cached
    url = OSV_VULN + urllib.parse.quote(vuln_id, safe='')
    request = urllib.request.Request(
        url,
        headers={'User-Agent': 'ergoms-deps-audit'},
    )
    try:
        with urllib.request.urlopen(request, timeout=OSV_TIMEOUT_SEC) as response:
            full = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        cache[vuln_id] = vuln
        return vuln
    if not isinstance(full, dict):
        cache[vuln_id] = vuln
        return vuln
    cache[vuln_id] = full
    return full


def _query_osv(
    ecosystem: str,
    packages: list[tuple[str, str]],
    *,
    source: str = 'lock',
) -> list[dict]:
    if not packages:
        return []
    findings: list[dict] = []
    cache: dict[str, dict] = {}
    chunk_size = 80
    for offset in range(0, len(packages), chunk_size):
        chunk = packages[offset:offset + chunk_size]
        body = json.dumps({
            'queries': [
                {'package': {'name': name, 'ecosystem': ecosystem}, 'version': version}
                for name, version in chunk
            ]
        }).encode('utf-8')
        request = urllib.request.Request(
            OSV_QUERYBATCH,
            data=body,
            headers={'Content-Type': 'application/json', 'User-Agent': 'ergoms-deps-audit'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=OSV_TIMEOUT_SEC) as response:
                payload = json.loads(response.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(format_console('warning', t('deps_audit_osv_failed', error=str(exc))))
            return findings
        results = payload.get('results') or []
        for index, result in enumerate(results):
            name, version = chunk[index]
            for vuln in result.get('vulns') or []:
                if not isinstance(vuln, dict):
                    continue
                full = _hydrate_osv_vuln(vuln, cache)
                severity, score = _osv_severity(full)
                findings.append({
                    'ecosystem': ecosystem,
                    'package': name,
                    'version': version,
                    'id': full.get('id') or vuln.get('id') or '',
                    'summary': (full.get('summary') or full.get('id') or vuln.get('id') or '').strip(),
                    'severity': severity,
                    'score': score,
                    'source': source,
                })
    return findings


def _npm_audit(project_root: Path) -> list[dict]:
    npm_root = npm_root_dir(project_root)
    if not (npm_root / 'package-lock.json').is_file():
        print(format_console('skip', t('deps_audit_npm_lock_missing')))
        return []
    node_dir = project_root / 'virtual_env' / 'packages' / 'nodejs'
    env = os.environ.copy()
    bin_dir = node_dir / ('bin' if os.name != 'nt' else '')
    if node_dir.is_dir():
        env['PATH'] = str(node_dir if os.name == 'nt' else bin_dir) + os.pathsep + env.get('PATH', '')
    npm_cmd = 'npm.cmd' if os.name == 'nt' else 'npm'
    result = subprocess.run(
        [npm_cmd, 'audit', '--json', '--omit=dev'],
        cwd=str(npm_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if not result.stdout.strip():
        if result.returncode != 0:
            print(format_console('warning', t('deps_audit_npm_failed', error=(result.stderr or '')[:300])))
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(format_console('warning', t('deps_audit_npm_failed', error='invalid JSON')))
        return []
    findings: list[dict] = []
    vulnerabilities = payload.get('vulnerabilities') or {}
    if isinstance(vulnerabilities, dict):
        for name, info in vulnerabilities.items():
            if not isinstance(info, dict):
                continue
            severity = str(info.get('severity') or 'info').upper()
            via = info.get('via') or []
            advisory = ''
            if isinstance(via, list) and via:
                first = via[0]
                if isinstance(first, dict):
                    advisory = str(first.get('url') or first.get('source') or '')
                    if first.get('title'):
                        advisory = str(first.get('title'))
            findings.append({
                'ecosystem': 'npm',
                'package': name,
                'version': str(info.get('range') or ''),
                'id': advisory,
                'summary': advisory or name,
                'severity': severity if severity != 'MODERATE' else 'MODERATE',
                'score': 0.0,
            })
    return findings


def _print_findings(findings: list[dict]) -> int:
    fail_count = 0
    warn_count = 0
    for item in sorted(findings, key=lambda row: (row['severity'], row['package'])):
        severity = item['severity']
        line = (
            f"{item['ecosystem']} {item['package']}@{item['version']}: "
            f"{item['id']} {item['summary']}"
        )
        if severity in FAIL_SEVERITIES and item.get('source') != 'venv':
            print(format_console('error', t('deps_audit_finding_high', detail=line)))
            fail_count += 1
        else:
            print(format_console('warning', t('deps_audit_finding_other', severity=severity, detail=line)))
            warn_count += 1
    print(format_console(
        'info',
        t('deps_audit_summary', high=fail_count, other=warn_count, total=len(findings)),
    ))
    if fail_count:
        print(format_console('error', t('deps_audit_failed')))
        return 1
    print(format_console('ok', t('deps_audit_ok')))
    return 0


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    root = _project_root()
    print(format_console('info', t('deps_audit_start')))
    python_pkgs = _parse_poetry_lock(root / 'poetry.lock')
    print(format_console('info', t('deps_audit_python_count', count=len(python_pkgs))))
    findings = _query_osv('PyPI', python_pkgs)
    venv_python = virtual_env_dir(root) / ('python/Scripts/python.exe' if os.name == 'nt' else 'python/bin/python')
    if venv_python.is_file():
        extra = subprocess.run(
            [str(venv_python), '-m', 'pip', 'list', '--format=json'],
            capture_output=True,
            text=True,
            check=False,
        )
        if extra.returncode == 0 and extra.stdout.strip():
            try:
                installed = [
                    (str(row.get('name')), str(row.get('version')))
                    for row in json.loads(extra.stdout)
                    if row.get('name') and row.get('version')
                ]
                locked = {(name.lower(), version) for name, version in python_pkgs}
                extra_pkgs = [
                    (name, version)
                    for name, version in installed
                    if (name.lower(), version) not in locked
                ]
                if extra_pkgs:
                    print(format_console('info', t('deps_audit_venv_extra', count=len(extra_pkgs))))
                    findings.extend(_query_osv('PyPI', extra_pkgs, source='venv'))
            except json.JSONDecodeError:
                pass
    findings.extend(_npm_audit(root))
    return _print_findings(findings)


if __name__ == '__main__':
    raise SystemExit(main())
