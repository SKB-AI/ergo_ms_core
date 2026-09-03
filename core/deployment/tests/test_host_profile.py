from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from lifecycle.host_process_guard import (  # noqa: E402
    host_wants_service,
    refuse_unwanted_core_service,
)
from lifecycle.host_profile import (  # noqa: E402
    DOCKER_PROFILE_API,
    DOCKER_PROFILE_BEAT,
    DOCKER_PROFILE_MEDIA,
    PROFILE_CORE,
    PROFILE_FULL,
    PROFILE_MODULES,
    SERVICE_API,
    SERVICE_BEAT,
    SERVICE_CLIENT,
    SERVICE_MEDIA,
    SERVICE_MODULE_API,
    SERVICE_MODULE_BEAT,
    SERVICE_MODULE_WORKER,
    SERVICE_YAML_WORKERS,
    resolve_host_profile,
)


class HostProfileTests(unittest.TestCase):
    def test_default_is_full(self) -> None:
        profile = resolve_host_profile({})
        self.assertEqual(profile.name, PROFILE_FULL)
        self.assertTrue(profile.wants(SERVICE_API))
        self.assertTrue(profile.wants(SERVICE_YAML_WORKERS))
        self.assertTrue(profile.wants(SERVICE_MODULE_API))

    def test_unknown_profile_is_full(self) -> None:
        profile = resolve_host_profile({'HOST_PROFILE': 'weird'})
        self.assertEqual(profile.name, PROFILE_FULL)

    def test_core_skips_module_services(self) -> None:
        profile = resolve_host_profile({'HOST_PROFILE': PROFILE_CORE})
        self.assertTrue(profile.wants(SERVICE_API))
        self.assertTrue(profile.wants(SERVICE_BEAT))
        self.assertFalse(profile.wants(SERVICE_MODULE_API))
        self.assertFalse(profile.wants(SERVICE_MODULE_WORKER))
        self.assertFalse(profile.wants(SERVICE_MODULE_BEAT))

    def test_modules_skips_core_api_beat_yaml(self) -> None:
        profile = resolve_host_profile({'HOST_PROFILE': PROFILE_MODULES})
        self.assertFalse(profile.wants(SERVICE_API))
        self.assertFalse(profile.wants(SERVICE_BEAT))
        self.assertFalse(profile.wants(SERVICE_YAML_WORKERS))
        self.assertTrue(profile.wants(SERVICE_MODULE_API))
        self.assertTrue(profile.wants(SERVICE_MODULE_WORKER))
        self.assertTrue(profile.wants(SERVICE_MODULE_BEAT))
        self.assertEqual(profile.core_unit_names(), ())
        self.assertEqual(profile.docker_compose_profiles(), ())

    def test_auto_becomes_modules_when_satellite_keys_set(self) -> None:
        profile = resolve_host_profile({
            'HOST_PROFILE': 'auto',
            'NGINX_API_UPSTREAM': '10.0.0.2:8000',
            'MICROSERVICE_MODULES': 'demo_mod',
            'BRIDGE_CORE_URL': 'http://10.0.0.2:8000',
        })
        self.assertEqual(profile.name, PROFILE_MODULES)
        self.assertFalse(profile.wants(SERVICE_API))

    def test_auto_stays_full_without_upstream(self) -> None:
        profile = resolve_host_profile({
            'HOST_PROFILE': 'auto',
            'MICROSERVICE_MODULES': 'demo_mod',
            'BRIDGE_CORE_URL': 'http://10.0.0.2:8000',
        })
        self.assertEqual(profile.name, PROFILE_FULL)

    def test_host_services_override(self) -> None:
        profile = resolve_host_profile({
            'HOST_PROFILE': PROFILE_FULL,
            'HOST_SERVICES': 'media,module_api',
        })
        self.assertTrue(profile.wants(SERVICE_MEDIA))
        self.assertTrue(profile.wants(SERVICE_MODULE_API))
        self.assertFalse(profile.wants(SERVICE_API))
        self.assertFalse(profile.wants(SERVICE_YAML_WORKERS))

    def test_host_media_auto_remote(self) -> None:
        profile = resolve_host_profile({
            'HOST_PROFILE': PROFILE_FULL,
            'HOST_MEDIA': 'auto',
            'ERGO_MEDIA': 'remote',
        })
        self.assertFalse(profile.wants(SERVICE_MEDIA))

    def test_host_celery_workers_modules(self) -> None:
        profile = resolve_host_profile({
            'HOST_PROFILE': PROFILE_FULL,
            'HOST_CELERY_WORKERS': 'modules',
        })
        self.assertFalse(profile.wants(SERVICE_YAML_WORKERS))
        self.assertTrue(profile.wants(SERVICE_MODULE_WORKER))
        self.assertTrue(profile.wants(SERVICE_MODULE_BEAT))

    def test_docker_profiles_for_full(self) -> None:
        profile = resolve_host_profile({'HOST_PROFILE': PROFILE_FULL})
        self.assertEqual(
            profile.docker_compose_profiles(),
            (DOCKER_PROFILE_API, DOCKER_PROFILE_MEDIA, DOCKER_PROFILE_BEAT),
        )

    def test_core_units_follow_service_prefix(self) -> None:
        profile = resolve_host_profile({
            'HOST_PROFILE': PROFILE_FULL,
            'ERGO_SERVICE_PREFIX': 'ergo_st_hp',
        })
        self.assertIn('ergo_st_hp_api_dev', profile.core_unit_names())
        self.assertNotIn('ergo_ms_api_dev', profile.core_unit_names())

    def test_modules_host_does_not_want_core_api(self) -> None:
        env = {'HOST_PROFILE': PROFILE_MODULES}
        self.assertFalse(host_wants_service(SERVICE_API, environ=env))
        self.assertFalse(host_wants_service(SERVICE_BEAT, environ=env))
        self.assertFalse(host_wants_service(SERVICE_CLIENT, environ=env))

    def test_refuse_core_api_when_host_skips_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('HOST_PROFILE=modules\n', encoding='utf-8')
            code = refuse_unwanted_core_service(
                SERVICE_API,
                message_key='host_refuses_core_api',
                project_root=root,
            )
            self.assertEqual(code, 1)

    def test_host_services_api_allows_core_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text(
                'HOST_PROFILE=modules\nHOST_SERVICES=api\n',
                encoding='utf-8',
            )
            code = refuse_unwanted_core_service(
                SERVICE_API,
                message_key='host_refuses_core_api',
                project_root=root,
            )
            self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
