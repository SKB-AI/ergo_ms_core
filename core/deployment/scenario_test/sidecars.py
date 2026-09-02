"""Одноразовые Docker-контейнеры MySQL/MSSQL для хостовых сценариев."""

from __future__ import annotations

import shutil
import subprocess
import time

from scenario_test.stack import (
    MYSQL_IMAGE,
    MSSQL_IMAGE,
    SCENARIO_DB_NAME,
    SCENARIO_MSSQL_PASSWORD,
    SCENARIO_MYSQL_PASSWORD,
)


class DockerUnavailable(Exception):
    pass


class SidecarFailed(Exception):
    pass


def docker_available() -> bool:
    if shutil.which('docker') is None:
        return False
    probe = subprocess.run(
        ['docker', 'info'],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return probe.returncode == 0


def stop_throwaway_container(name: str) -> None:
    subprocess.run(
        ['docker', 'rm', '-f', name],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _run_container(cmd: list[str]) -> None:
    started = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if started.returncode != 0:
        detail = (started.stderr or started.stdout or 'docker run failed').strip()
        raise SidecarFailed(detail[-1500:])


def start_throwaway_mysql(name: str, port: int) -> None:
    if not docker_available():
        raise DockerUnavailable('docker')
    stop_throwaway_container(name)
    _run_container(
        [
            'docker',
            'run',
            '-d',
            '--name',
            name,
            '-p',
            f'127.0.0.1:{int(port)}:3306',
            '-e',
            f'MYSQL_ROOT_PASSWORD={SCENARIO_MYSQL_PASSWORD}',
            '-e',
            f'MYSQL_DATABASE={SCENARIO_DB_NAME}',
            MYSQL_IMAGE,
        ]
    )
    _wait_mysql(name)


def start_throwaway_mssql(name: str, port: int) -> None:
    if not docker_available():
        raise DockerUnavailable('docker')
    stop_throwaway_container(name)
    _run_container(
        [
            'docker',
            'run',
            '-d',
            '--name',
            name,
            '-p',
            f'127.0.0.1:{int(port)}:1433',
            '-e',
            'ACCEPT_EULA=Y',
            '-e',
            f'MSSQL_SA_PASSWORD={SCENARIO_MSSQL_PASSWORD}',
            MSSQL_IMAGE,
        ]
    )
    _wait_mssql(name)
    ensure_mssql_database(name)


def _wait_mysql(name: str) -> None:
    ping = [
        'docker',
        'exec',
        name,
        'mysqladmin',
        'ping',
        '-h',
        '127.0.0.1',
        '-uroot',
        f'-p{SCENARIO_MYSQL_PASSWORD}',
        '--silent',
    ]
    for _ in range(40):
        result = subprocess.run(ping, capture_output=True, timeout=15, check=False)
        if result.returncode == 0:
            return
        time.sleep(2)
    raise SidecarFailed('mysql sidecar did not become ready')


def _wait_mssql(name: str) -> None:
    query = [
        'docker',
        'exec',
        name,
        '/opt/mssql-tools18/bin/sqlcmd',
        '-C',
        '-S',
        'localhost',
        '-U',
        'sa',
        '-P',
        SCENARIO_MSSQL_PASSWORD,
        '-Q',
        'SELECT 1',
    ]
    fallback = [
        'docker',
        'exec',
        name,
        '/opt/mssql-tools/bin/sqlcmd',
        '-S',
        'localhost',
        '-U',
        'sa',
        '-P',
        SCENARIO_MSSQL_PASSWORD,
        '-Q',
        'SELECT 1',
    ]
    for _ in range(45):
        result = subprocess.run(query, capture_output=True, timeout=20, check=False)
        if result.returncode == 0:
            return
        result = subprocess.run(fallback, capture_output=True, timeout=20, check=False)
        if result.returncode == 0:
            return
        time.sleep(2)
    raise SidecarFailed('mssql sidecar did not become ready')


def ensure_mssql_database(name: str) -> None:
    sql = f"IF DB_ID(N'{SCENARIO_DB_NAME}') IS NULL CREATE DATABASE [{SCENARIO_DB_NAME}];"
    for binary in ('/opt/mssql-tools18/bin/sqlcmd', '/opt/mssql-tools/bin/sqlcmd'):
        extra = ['-C'] if 'tools18' in binary else []
        result = subprocess.run(
            [
                'docker',
                'exec',
                name,
                binary,
                *extra,
                '-S',
                'localhost',
                '-U',
                'sa',
                '-P',
                SCENARIO_MSSQL_PASSWORD,
                '-Q',
                sql,
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return
    raise SidecarFailed('could not create mssql database')
