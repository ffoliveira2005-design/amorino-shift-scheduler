"""Production checks must fail closed without importing/migrating the app."""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from scripts import preflight
from scripts.ops_common import APP_TABLES, ROOT


def configured(path):
    return {'AMORINO_SECRET_KEY': '0123456789abcdef' * 4,
            'AMORINO_SUPERVISOR_PIN': 'pilot-supervisor-5729',
            'AMORINO_DB_PATH': str(path), 'AMORINO_HTTPS': '1',
            'AMORINO_TRUST_PROXY': '1', 'AMORINO_HOST': '127.0.0.1',
            'AMORINO_PROCESSES': '1', 'AMORINO_STORAGE': 'local', 'TZ': 'Europe/Lisbon',
            'AMORINO_SEED': '0'}


def database(path):
    connection = sqlite3.connect(path)
    try:
        for table in APP_TABLES:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.commit()
    finally:
        connection.close()


def test_valid_configuration_and_database_are_read_only(tmp_path):
    # Full-suite --basetemp may live inside the checkout. Production preflight
    # correctly rejects that; use a dedicated outside-checkout disposable path.
    with tempfile.TemporaryDirectory(prefix='amorino-preflight-') as directory:
        path = Path(directory) / 'preflight.db'
        database(path)
        before = path.read_bytes()
        assert preflight.check(configured(path))['ok']
        assert path.read_bytes() == before


@pytest.mark.parametrize('name,value', [
    ('AMORINO_SECRET_KEY', 'amorino-gelato-2026'), ('AMORINO_SUPERVISOR_PIN', '1234'),
    ('AMORINO_HTTPS', '0'), ('AMORINO_TRUST_PROXY', '0'), ('AMORINO_DEBUG', '1'),
    ('AMORINO_SEED', '1'),
    ('FLASK_DEBUG', 'true'), ('AMORINO_DEMO', 'yes'), ('AMORINO_HOST', '0.0.0.0'),
    ('AMORINO_PROCESSES', '2'), ('WEB_CONCURRENCY', '4'), ('AMORINO_WORKERS', '2'),
    ('GUNICORN_CMD_ARGS', '--workers=4'), ('GUNICORN_CMD_ARGS', '-w 2'),
    ('AMORINO_STORAGE', 'nfs'), ('TZ', 'UTC'), ('AMORINO_THREADS', '0'),
    ('AMORINO_PORT', 'oops'), ('AMORINO_DB_PATH', 'relative.db'),
])
def test_unsafe_configuration_rejected_without_disclosing_secret(tmp_path, name, value):
    env = configured(tmp_path / 'scratch.db')
    env[name] = value
    result = preflight.check(env, inspect_database=False)
    assert not result['ok']
    assert env['AMORINO_SECRET_KEY'] not in json.dumps(result)


def test_repository_live_database_is_never_opened(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Repository database must not be opened')
    monkeypatch.setattr(preflight, 'validate_database', forbidden)
    result = preflight.check(configured(ROOT / 'amorino.db'))
    assert not result['ok']
    assert any('not opened' in message for message in result['errors'])


def test_missing_database_not_created(tmp_path):
    missing = tmp_path / 'missing.db'
    assert not preflight.check(configured(missing))['ok']
    assert not missing.exists()


@pytest.mark.parametrize('marker', ['restore-in-progress', 'migration-in-progress'])
def test_incomplete_operation_marker_prevents_service_start(tmp_path, monkeypatch, marker):
    with tempfile.TemporaryDirectory(prefix='amorino-preflight-') as directory:
        path = Path(directory) / 'restore.db'
        database(path)
        path.with_name(path.name + '.' + marker).write_text('{}')
        def forbidden(*args, **kwargs):
            raise AssertionError('Incomplete operations must block before opening SQLite')
        monkeypatch.setattr(preflight, 'validate_database', forbidden)
        result = preflight.check(configured(path))
        assert not result['ok']
        assert any('validation failed' in message for message in result['errors'])
