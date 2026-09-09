from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import _bootstrap  # noqa: F401

from lifecycle.services.catalog import list_core_services  # noqa: E402
from service_names import (  # noqa: E402
    API_DEV,
    CELERY_BEAT,
    CLIENT_DEV,
    MEDIA_API,
    NGINX,
    POSTGRES,
    REDIS,
)

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[1]
_CORE_PACKAGES = _DEPLOYMENT_DIR / 'packages' / 'core_packages.yaml'
_NSSM = _DEPLOYMENT_DIR / 'windows' / 'lib' / 'nssm.ps1'
_SYSTEMD = _DEPLOYMENT_DIR / 'linux' / 'lib' / 'systemd.sh'
_SERVICES_PS1 = _DEPLOYMENT_DIR / 'windows' / 'lib' / 'services.ps1'
_SERVICES_SH = _DEPLOYMENT_DIR / 'linux' / 'lib' / 'services.sh'

_START_SCRIPTS = (
    'start_api.py',
    'start_media_api.py',
    'start_celery_beat.py',
    'start_celery_worker.py',
    'start_client_if_dev.py',
)

_CORE_SERVICE_NAMES = (API_DEV, CLIENT_DEV, MEDIA_API, CELERY_BEAT)


class PortablePackageMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packages = yaml.safe_load(_CORE_PACKAGES.read_text(encoding='utf-8'))['packages']

    def test_custom_packages_have_windows_and_linux_markers(self) -> None:
        missing: list[str] = []
        for name, spec in self.packages.items():
            if spec.get('kind') != 'custom':
                continue
            if not spec.get('marker_windows') or not spec.get('marker_linux'):
                missing.append(name)
        self.assertEqual(missing, [], msg=f'custom packages without both markers: {missing}')

    def test_archive_packages_declare_platforms(self) -> None:
        for name, spec in self.packages.items():
            if spec.get('kind') != 'archive':
                continue
            platforms = spec.get('platforms') or {}
            self.assertTrue(platforms, msg=f'{name} archive has no platforms')
            for platform, payload in platforms.items():
                self.assertTrue(payload.get('marker'), msg=f'{name}.{platform} missing marker')
                self.assertTrue(payload.get('url'), msg=f'{name}.{platform} missing url')
            if name == 'nssm':
                self.assertIn('windows', platforms)
                self.assertNotIn('linux', platforms)


class ServiceNameParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.nssm = _NSSM.read_text(encoding='utf-8')
        cls.systemd = _SYSTEMD.read_text(encoding='utf-8')
        cls.services_ps1 = _SERVICES_PS1.read_text(encoding='utf-8')
        cls.services_sh = _SERVICES_SH.read_text(encoding='utf-8')

    def test_core_service_names_match_catalog(self) -> None:
        catalog_names = {entry.unit_name for entry in list_core_services()}
        self.assertEqual(catalog_names, set(_CORE_SERVICE_NAMES))

    def test_core_services_exist_in_nssm_and_systemd(self) -> None:
        for suffix in ('_api_dev', '_client_dev', '_media_api', '_celery_beat'):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, self.nssm)
        self.assertIn('Get-ErgoServiceName', self.services_ps1)
        self.assertIn('ergo_service_name', self.services_sh)
        self.assertIn('API_UNIT', self.systemd)
        self.assertIn('CLIENT_UNIT', self.systemd)
        self.assertIn('CELERY_BEAT_UNIT', self.systemd)
        self.assertIn('MEDIA_API_UNIT', self.systemd)

    def test_start_scripts_shared_by_nssm_and_systemd(self) -> None:
        for script in _START_SCRIPTS:
            with self.subTest(script=script):
                self.assertIn(script, self.nssm)
                self.assertIn(script, self.systemd)

    def test_app_units_take_user_from_project_owner(self) -> None:
        self.assertIn('_systemd_project_user_block', self.systemd)
        self.assertIn('user_block="$(_systemd_project_user_block', self.systemd)
        self.assertIn('${user_block}\nEnvironmentFile=', self.systemd.replace('\r\n', '\n'))
        self.assertNotIn('User=administrator', self.systemd)
        self.assertNotIn('User=root', self.systemd)

    @unittest.skipUnless(os.name != 'nt', 'Linux systemd unit generation')
    def test_generated_api_unit_keeps_user_and_envfile_apart(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'logs').mkdir()
            script = f'''
LIB="{_DEPLOYMENT_DIR / 'linux' / 'lib'}"
# shellcheck disable=SC1091
source "$LIB/core.sh"
# shellcheck disable=SC1091
source "$LIB/systemd.sh"
get_base_unit_definitions "{root}"
printf '%s' "$API_UNIT"
'''
            result = subprocess.run(
                ['bash', '-c', script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            text = result.stdout
            self.assertIn('\nUser=', text)
            self.assertIn('\nGroup=', text)
            self.assertIn('\nEnvironmentFile=', text)
            self.assertNotIn('administratorEnvironmentFile', text)
            self.assertNotRegex(text, r'User=\S+EnvironmentFile=')

    def test_infra_service_names_aligned(self) -> None:
        self.assertIn('Get-ErgoServiceName', self.services_ps1)
        self.assertIn('ergo_service_name redis', self.services_sh)
        self.assertIn('ergo_service_name nginx', self.services_sh)


if __name__ == '__main__':
    unittest.main()
