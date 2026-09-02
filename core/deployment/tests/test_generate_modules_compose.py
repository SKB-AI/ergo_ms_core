from __future__ import annotations

import unittest

import yaml

import _bootstrap  # noqa: F401

from generate_modules_compose import generate, module_port, parse_modules  # noqa: E402


class GenerateModulesComposeTests(unittest.TestCase):
    def test_parse_modules_csv(self) -> None:
        self.assertEqual(parse_modules('demo_mod, other'), ['demo_mod', 'other'])
        self.assertEqual(parse_modules(''), [])
        self.assertEqual(parse_modules('  ,  ,'), [])

    def test_monolith_yaml_is_empty_services(self) -> None:
        text = generate([])
        data = yaml.safe_load(text)
        self.assertEqual(data.get('services'), {})

    def test_microservice_service_and_worker(self) -> None:
        environ = {'DEMO_MOD_PORT': '8123'}
        text = generate(['demo_mod'], environ)
        data = yaml.safe_load(text)
        services = data['services']
        self.assertIn('demo_mod', services)
        self.assertIn('demo_mod-worker', services)
        api = services['demo_mod']
        self.assertEqual(api['command'], ['python', 'core/api/scripts/start_module_api.py', '--module=demo_mod'])
        self.assertIn('8123', api['expose'])
        self.assertIn('api', api['depends_on'])
        worker = services['demo_mod-worker']
        self.assertEqual(
            worker['command'],
            ['python', 'core/api/scripts/start_celery_worker.py', '--module=demo_mod'],
        )
        self.assertNotIn('demo_mod-beat', services)

    def test_microservice_beat_when_requested(self) -> None:
        text = generate(['demo_mod'], {'DEMO_MOD_PORT': '8123'}, beat_modules=frozenset({'demo_mod'}))
        data = yaml.safe_load(text)
        beat = data['services']['demo_mod-beat']
        self.assertEqual(
            beat['command'],
            ['python', 'core/api/scripts/start_celery_beat.py', '--module=demo_mod'],
        )

    def test_modules_host_omits_api_depends(self) -> None:
        text = generate(['demo_mod'], {'DEMO_MOD_PORT': '8123'}, depends_on_api=False)
        data = yaml.safe_load(text)
        depends = data['services']['demo_mod']['depends_on']
        self.assertIn('redis', depends)
        self.assertNotIn('api', depends)

    def test_healthcheck_is_cmd_list_not_shell(self) -> None:
        text = generate(['demo_mod'], {'DEMO_MOD_PORT': '8123'})
        data = yaml.safe_load(text)
        probe = data['services']['demo_mod']['healthcheck']['test']
        self.assertIsInstance(probe, list)
        self.assertEqual(probe[0], 'CMD')
        self.assertEqual(probe[1], 'python')
        self.assertEqual(probe[2], '-c')
        self.assertIn('127.0.0.1:8123', probe[3])
        self.assertNotIn('CMD-SHELL', probe)

    def test_module_port_from_env(self) -> None:
        self.assertEqual(module_port('demo_mod', {'DEMO_MOD_PORT': '8123'}), '8123')

    def test_module_port_stable_fallback(self) -> None:
        first = module_port('demo_mod', {})
        second = module_port('demo_mod', {})
        self.assertEqual(first, second)
        self.assertTrue(first.isdigit())


if __name__ == '__main__':
    unittest.main()
