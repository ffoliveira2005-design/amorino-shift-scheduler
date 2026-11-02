"""Controlled launch migration rehearsals only; never reads repository live data."""
import os
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.migrate_launch import _validated_database, run
from scripts.migration_rehearsal import create_legacy_fixture, rehearse
from scripts.ops_common import ROOT


def test_migration_refuses_root_database_before_any_read(monkeypatch):
    target = ROOT / 'amorino.db'
    monkeypatch.setenv('AMORINO_DB_PATH', str(target))
    with pytest.raises(ValueError, match='prohibited'):
        _validated_database(target)


def test_migration_requires_explicit_matching_path_and_offline_confirmation(tmp_path, monkeypatch):
    target = tmp_path / 'fictional.db'
    create_legacy_fixture(target)
    monkeypatch.delenv('AMORINO_DB_PATH', raising=False)
    with pytest.raises(ValueError, match='AMORINO_DB_PATH'):
        run(target)
    monkeypatch.setenv('AMORINO_DB_PATH', str(target))
    with pytest.raises(ValueError, match='absolute'):
        _validated_database('fictional.db')
    with pytest.raises(ValueError, match='service-stopped'):
        run(target, apply=True)
    with pytest.raises(ValueError, match='confirm-target'):
        run(target, apply=True, service_stopped=True, confirmed_target=str(tmp_path / 'wrong.db'))
    with pytest.raises(ValueError, match='backup-dir'):
        run(target, apply=True, service_stopped=True, confirmed_target=str(target))
    with sqlite3.connect(target) as connection:
        assert 'access_version' not in {row[1] for row in connection.execute('PRAGMA table_info(funcionarios)')}


def test_fictional_legacy_rehearsal_preserves_data_and_versions(tmp_path, monkeypatch):
    monkeypatch.setenv('AMORINO_DB_PATH', str(tmp_path / 'explicit-placeholder.db'))
    result = rehearse(tmp_path / 'fictional-rehearsal')
    assert result['ok'] and not result['source_migrated']
    assert all(result['checks'].values())
    assert result['original_fictional_source_unmodified']
    assert result['existing_secure_pin_verified'] and result['unclaimed_account_stays_locked']
    assert result['counts_after']['disponibilidades'] == 42
    assert result['published_shift_count'] == result['published_delivery_count'] == 2
    assert result['future_remains_draft'] and not result['production_deployment_verified']


def test_guarded_apply_keeps_verified_pre_migration_backup(tmp_path, monkeypatch):
    source = tmp_path / 'fictional-apply.db'
    create_legacy_fixture(source)
    monkeypatch.setenv('AMORINO_DB_PATH', str(source))
    result = run(source, apply=True, service_stopped=True, confirmed_target=str(source),
                 backup_directory=tmp_path / 'rollback-backups', work_directory=tmp_path / 'apply-report')
    assert result['ok'] and result['source_migrated'] and result['backup_manifest_verified']
    assert Path(result['backup']).is_file() and Path(result['backup'] + '.json').is_file()
    assert not source.with_name(source.name + '.migration-in-progress').exists()
    with sqlite3.connect(result['backup']) as connection:
        assert 'access_version' not in {row[1] for row in connection.execute('PRAGMA table_info(funcionarios)')}
        assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='schedule_releases'").fetchone()[0] == 0
