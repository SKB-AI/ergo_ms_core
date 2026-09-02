from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

import yaml

import _bootstrap  # noqa: F401

from scenario_test.live_stack import app_run_commands, exec_command, infra_run_commands  # noqa: E402
from scenario_test.stack import (  # noqa: E402
    COMPOSE_PROJECT,
    RUNTIME_ENV_NAME,
    write_compose_file,
    write_nginx_conf,
    write_runtime_env,
)


class ScenarioStackTests(unittest.TestCase):
    def test_compose_does_not_bind_repo_root_or_poetry_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            run_dir = Path(tmp) / 'run'
            for rel in (
                'core/api',
                'core/deployment',
                'core/media_api',
                'core/shared',
                'core/client/dist',
                'modules',
            ):
                (root / rel).mkdir(parents=True, exist_ok=True)
            for rel in ('logs', 'media', 'notebooks', 'jupyter', 'static_api', 'modules'):
                (run_dir / rel).mkdir(parents=True, exist_ok=True)
            (run_dir / 'databases.yaml').write_text('databases: {}\n', encoding='utf-8')
            (run_dir / 'nginx.conf').write_text('# nginx\n', encoding='utf-8')
            compose_path = run_dir / 'docker-compose.yml'
            write_compose_file(
                compose_path,
                project_root=root,
                run_dir=run_dir,
                ports={'api': 18000, 'nginx': 18080, 'jupyter': 18002, 'postgres': 15432},
                meili_key='meili-test-key',
                jupyter_token='jupyter-test-token',
            )
            text = compose_path.read_text(encoding='utf-8')
            data = yaml.safe_load(text)
            self.assertEqual(data['name'], COMPOSE_PROJECT)
            write_compose_file(
                compose_path,
                project_root=root,
                run_dir=run_dir,
                ports={'api': 18000, 'nginx': 18080, 'jupyter': 18002, 'postgres': 15432},
                meili_key='meili-test-key',
                jupyter_token='jupyter-test-token',
                project_name='ergo_ms_scenario2',
            )
            renamed = yaml.safe_load(compose_path.read_text(encoding='utf-8'))
            self.assertEqual(renamed['name'], 'ergo_ms_scenario2')
            self.assertEqual(renamed['networks']['scenario_net']['name'], 'ergo_ms_scenario2_net')
            self.assertNotIn('volumes', data)
            self.assertNotIn('scenario_poetry', text)
            self.assertIn('/app/core/api', text)
            self.assertIn('env_file:', text)
            root_bind = re.compile(r':/app(?::ro)?\s*$', re.M)
            self.assertIsNone(root_bind.search(text))
            api_volumes = '\n'.join(str(item) for item in data['services']['api']['volumes'])
            self.assertNotIn(str(root.resolve()).replace('\\', '/') + ':/app', api_volumes)
            self.assertTrue(any('/app/core/api' in str(item) for item in data['services']['api']['volumes']))
            self.assertTrue(any('/app/virtual_env/jupyter' in str(item) for item in data['services']['jupyter']['volumes']))
            self.assertFalse(
                any('/app/virtual_env/python' in str(item) for item in data['services']['api']['volumes'])
            )

    def test_runtime_env_is_throwaway_and_has_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            write_runtime_env(
                path,
                ports={'api': 18000, 'jupyter': 18002},
                api_secret='a' * 32,
                jwt_secret='b' * 32,
                media_internal_key='c' * 32,
                meili_key='d' * 16,
                jupyter_token='e' * 16,
            )
            text = path.read_text(encoding='utf-8')
            self.assertIn('API_SECRET_KEY=' + 'a' * 32, text)
            self.assertIn('ERGO_DOCKER_DB_PORT=5432', text)
            self.assertIn('REDIS_PORT=6379', text)
            self.assertIn('ERGO_EMAIL=none', text)
            self.assertIn('MODULE_RUNTIME=monolith', text)
            self.assertIn('ERGO_JUPYTER=nginx', text)
            self.assertNotIn('C:\\\\', text)


    def test_live_infra_uses_docker_run_not_compose_up(self) -> None:
        commands = infra_run_commands(
            project='ergo_ms_scenario',
            ports={'postgres': 15432},
            meili_key='k',
        )
        self.assertEqual(len(commands), 3)
        for cmd in commands:
            self.assertEqual(cmd[0:3], ['docker', 'run', '-d'])
            self.assertNotIn('compose', cmd)
            self.assertNotIn('--network', cmd)
            self.assertNotIn('-p', cmd)

    def test_live_infra_mysql_and_sqlite_skip_postgres(self) -> None:
        mysql_cmds = infra_run_commands(
            project='ergo_ms_scenario',
            ports={'mysql': 13306},
            meili_key='k',
            db='mysql',
            use_redis=True,
        )
        mysql_joined = ' '.join(' '.join(cmd) for cmd in mysql_cmds)
        self.assertIn('mysql:8', mysql_joined)
        self.assertNotIn('postgres:16-alpine', mysql_joined)
        sqlite_cmds = infra_run_commands(
            project='ergo_ms_scenario',
            ports={},
            meili_key='k',
            db='sqlite',
            use_redis=False,
        )
        sqlite_joined = ' '.join(' '.join(cmd) for cmd in sqlite_cmds)
        self.assertNotIn('postgres:16-alpine', sqlite_joined)
        self.assertNotIn('redis:7-alpine', sqlite_joined)
        self.assertIn('getmeili/meilisearch', sqlite_joined)

    def test_app_run_commands_use_add_host_without_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            run_dir = Path(tmp) / 'run'
            for rel in ('core/api', 'core/client/dist', 'core/deployment', 'core/media_api', 'core/shared', 'modules'):
                (root / rel).mkdir(parents=True, exist_ok=True)
            for rel in ('logs', 'media', 'notebooks', 'jupyter', 'static_api', 'modules'):
                (run_dir / rel).mkdir(parents=True, exist_ok=True)
            (run_dir / RUNTIME_ENV_NAME).write_text('API_PORT=18000\n', encoding='utf-8')
            commands = app_run_commands(
                project='ergo_ms_scenario',
                project_root=root,
                run_dir=run_dir,
                jupyter_token='tok',
                extra_hosts={'redis': '172.17.0.2', 'postgres': '172.17.0.3'},
                api_host='172.17.0.4',
                media_host='172.17.0.5',
            )
        self.assertEqual(len(commands), 4)
        joined = ' '.join(' '.join(cmd) for cmd in commands)
        self.assertIn('--add-host redis:172.17.0.2', joined)
        self.assertIn('--add-host api:172.17.0.4', joined)
        self.assertIn('--add-host media-api:172.17.0.5', joined)
        self.assertIn('--add-host jupyter:127.0.0.1', joined)
        self.assertIn('jupyter_boot.py', joined)
        self.assertNotIn('start_jupyter.py', joined)
        for cmd in commands:
            self.assertEqual(cmd[0:3], ['docker', 'run', '-d'])
            self.assertNotIn('--network', cmd)
            self.assertNotIn('-p', cmd)
            self.assertNotIn('compose', cmd)

    def test_app_run_commands_omit_jupyter_and_nginx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            run_dir = Path(tmp) / 'run'
            for rel in ('core/api', 'core/client/dist', 'core/deployment', 'core/media_api', 'core/shared', 'modules'):
                (root / rel).mkdir(parents=True, exist_ok=True)
            for rel in ('logs', 'media', 'notebooks', 'jupyter', 'static_api', 'modules'):
                (run_dir / rel).mkdir(parents=True, exist_ok=True)
            (run_dir / RUNTIME_ENV_NAME).write_text('API_PORT=18000\n', encoding='utf-8')
            commands = app_run_commands(
                project='ergo_ms_scenario',
                project_root=root,
                run_dir=run_dir,
                jupyter_token='tok',
                extra_hosts={'redis': '172.17.0.2'},
                api_host='172.17.0.4',
                media_host='172.17.0.5',
                include_jupyter=False,
                include_nginx=False,
            )
        self.assertEqual(len(commands), 2)
        joined = ' '.join(' '.join(cmd) for cmd in commands)
        self.assertNotIn('jupyter_boot.py', joined)
        self.assertNotIn('nginx:1.27', joined)

    def test_sqlite_databases_yaml_uses_file_engine(self) -> None:
        from scenario_test.stack import write_databases_yaml

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'databases.yaml'
            sqlite = Path(tmp) / 'scenario.sqlite3'
            write_databases_yaml(path, db='sqlite', sqlite_path=sqlite)
            text = path.read_text(encoding='utf-8')
        self.assertIn('engine: sqlite', text)
        self.assertNotIn('engine: postgresql', text)

    def test_mysql_and_mssql_databases_yaml_use_engine_and_loopback_port(self) -> None:
        from scenario_test.stack import write_databases_yaml

        with tempfile.TemporaryDirectory() as tmp:
            mysql_path = Path(tmp) / 'mysql.yaml'
            mssql_path = Path(tmp) / 'mssql.yaml'
            write_databases_yaml(mysql_path, db='mysql', db_host='127.0.0.1', db_port=13306)
            write_databases_yaml(mssql_path, db='mssql', db_host='127.0.0.1', db_port=11433)
            mysql_text = mysql_path.read_text(encoding='utf-8')
            mssql_text = mssql_path.read_text(encoding='utf-8')
        self.assertIn('engine: mysql', mysql_text)
        self.assertIn('port: 13306', mysql_text)
        self.assertNotIn('port: 3306', mysql_text)
        self.assertIn('engine: mssql', mssql_text)
        self.assertIn('port: 11433', mssql_text)
        self.assertNotIn('port: 1433', mssql_text)

    def test_build_host_env_does_not_inherit_workspace_env(self) -> None:
        from scenario_test.host_stack import build_host_env
        from scenario_test.isolation import workspace_config_fingerprint
        from scenario_test.matrix import all_specs

        spec = next(item for item in all_specs() if item.id == 'host_postgres_redis_nginx')
        probe = 'ERGO_SCENARIO_ISOLATION_PROBE'
        os.environ[probe] = 'unique-not-for-scenario'
        try:
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                env = build_host_env(
                    project_root=run_dir,
                    run_dir=run_dir,
                    spec=spec,
                    ports={
                        'api': 18000,
                        'nginx': 18080,
                        'jupyter': 18002,
                        'postgres': 15432,
                        'redis': 16379,
                        'media': 18103,
                        'module': 18200,
                        'mysql': 13306,
                        'mssql': 11433,
                    },
                )
                (run_dir / '.env').write_text('A=1\n', encoding='utf-8')
                (run_dir / 'databases.yaml').write_text('databases: {}\n', encoding='utf-8')
                first = workspace_config_fingerprint(run_dir)
                second = workspace_config_fingerprint(run_dir)
        finally:
            del os.environ[probe]
        self.assertNotIn(probe, env)
        self.assertEqual(first, second)
        self.assertNotEqual(first['.env'], '')

    def test_exec_command_is_plain_docker_exec(self) -> None:
        cmd = exec_command('box', 'redis-cli', 'ping')
        self.assertEqual(
            cmd[:3],
            ['docker', 'exec', '-e'],
        )
        self.assertEqual(cmd[-3:], ['box', 'redis-cli', 'ping'])
        self.assertNotIn('-T', cmd)

    def test_core_http_cases_cover_loadtest_yaml(self) -> None:
        from scenario_test.http_checks import load_core_http_cases

        path = Path(__file__).resolve().parents[1] / 'loadtest' / 'core_scenarios.yaml'
        cases = load_core_http_cases(path)
        ids = {item.case_id for item in cases}
        self.assertIn('session_bootstrap', ids)
        self.assertIn('my_permissions', ids)
        self.assertIn('notifications_prefs_patch', ids)
        self.assertIn('notifications_sources', ids)
        self.assertIn('devices', ids)
        self.assertIn('themes_catalog', ids)
        self.assertTrue(any(item.path == '/api/cms/adp/session-bootstrap/' for item in cases))
        self.assertTrue(any(item.method == 'PATCH' for item in cases))
        self.assertGreaterEqual(len(cases), 12)

    def test_jupyter_boot_module_exists(self) -> None:
        boot = Path(__file__).resolve().parents[1] / 'scenario_test' / 'jupyter_boot.py'
        self.assertTrue(boot.is_file())
        text = boot.read_text(encoding='utf-8')
        self.assertIn('--with', text)
        self.assertIn('jupyter', text)
        self.assertIn('start_jupyter.py', text)

    def test_nginx_conf_uses_jupyter_ip_and_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nginx.conf'
            write_nginx_conf(
                path,
                api_port=18000,
                nginx_port=18080,
                api_upstream='172.17.0.5',
                media_upstream='172.17.0.6',
                jupyter_upstream='172.17.0.8',
                jupyter_port=18002,
            )
            text = path.read_text(encoding='utf-8')
        self.assertIn('http://172.17.0.8:18002/jupyter/', text)
        self.assertNotIn('http://jupyter:', text)


if __name__ == '__main__':
    unittest.main()
