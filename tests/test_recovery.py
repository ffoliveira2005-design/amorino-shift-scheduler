"""Isolated backup, restore safety and complete fictional application rehearsal."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts.backup_db import backup, prune
from scripts.offserver_plan import build_plan
from scripts.ops_common import APP_TABLES, ROOT, sha256_file
from scripts.restore_db import restore
from scripts.verify_backup import verify


def fictional_db(path):
    connection = sqlite3.connect(path)
    try:
        for table in APP_TABLES:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, note TEXT)')
        connection.execute("INSERT INTO funcionarios (note) VALUES ('Fictional person')")
        connection.execute("INSERT INTO turnos (note) VALUES ('Fictional shift')")
        connection.commit()
    finally:
        connection.close()


def test_online_backup_includes_committed_wal_and_manifest(tmp_path, monkeypatch):
    source = tmp_path / 'source.db'
    monkeypatch.setenv('AMORINO_DB_PATH', str(source))
    fictional_db(source)
    connection = sqlite3.connect(source)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute("INSERT INTO turnos (note) VALUES ('Committed in WAL')")
        connection.commit()
        assert Path(str(source) + '-wal').exists()
        snapshot = Path(backup(source, tmp_path / 'backups', 14))
        assert verify(snapshot, max_age_hours=1) >= 0
        with sqlite3.connect(snapshot) as copied:
            assert copied.execute('SELECT count(*) FROM turnos').fetchone()[0] == 2
        manifest = json.loads(Path(str(snapshot) + '.json').read_text())
        assert manifest['sha256'] == sha256_file(snapshot)
    finally:
        connection.close()


def test_backups_same_second_do_not_overwrite_and_prune_manifest(tmp_path, monkeypatch):
    source = tmp_path / 'source.db'
    monkeypatch.setenv('AMORINO_DB_PATH', str(source))
    fictional_db(source)
    first = Path(backup(source, tmp_path / 'backups', 1))
    second = Path(backup(source, tmp_path / 'backups', 1))
    assert first != second
    assert not first.exists() and not Path(str(first) + '.json').exists()
    assert second.exists() and Path(str(second) + '.json').exists()


def test_restore_preserves_damaged_database_and_all_sidecars(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.db', tmp_path / 'damaged.db'
    fictional_db(source)
    snapshot = backup(source, tmp_path / 'backups', 14)
    monkeypatch.setenv('AMORINO_DB_PATH', str(target))
    originals = {}
    for suffix in ('', '-wal', '-shm', '-journal'):
        path = Path(str(target) + suffix)
        originals[path.name] = ('fictional damage' + suffix).encode()
        path.write_bytes(originals[path.name])
    result = restore(snapshot, target, service_stopped=True, confirmed_target=target, allow_repository_target=True)
    for name, content in originals.items():
        assert (Path(result['preserved']) / name).read_bytes() == content
    with sqlite3.connect(target) as restored:
        assert restored.execute('SELECT count(*) FROM funcionarios').fetchone()[0] == 1
    assert not Path(str(target) + '.restore-in-progress').exists()


@pytest.mark.parametrize('guard', ['running', 'wrong-confirmation', 'wrong-environment', 'missing-manifest', 'checksum'])
def test_restore_guards_never_change_target(tmp_path, monkeypatch, guard):
    source, target = tmp_path / 'source.db', tmp_path / 'target.db'
    fictional_db(source)
    snapshot = Path(backup(source, tmp_path / 'backups', 14))
    target.write_bytes(b'original fictional damaged data')
    monkeypatch.setenv('AMORINO_DB_PATH', str(target if guard != 'wrong-environment' else source))
    if guard == 'missing-manifest':
        Path(str(snapshot) + '.json').unlink()
    if guard == 'checksum':
        with snapshot.open('ab') as handle:
            handle.write(b'changed')
    with pytest.raises(ValueError):
        restore(snapshot, target, service_stopped=guard != 'running',
                confirmed_target=source if guard == 'wrong-confirmation' else target,
                allow_repository_target=True)
    assert target.read_bytes() == b'original fictional damaged data'
    assert not list(tmp_path.glob('damaged-*'))


def test_stale_backup_fails_even_with_valid_database(tmp_path):
    source = tmp_path / 'source.db'
    fictional_db(source)
    snapshot = Path(backup(source, tmp_path / 'backups', 14))
    manifest_path = Path(str(snapshot) + '.json')
    manifest = json.loads(manifest_path.read_text())
    manifest['snapshot_started_utc'] = (datetime.now(timezone.utc) - timedelta(hours=28)).isoformat()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='older'):
        verify(snapshot, max_age_hours=26)


def test_restore_never_resurrects_invitation_calendar_or_session_version(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.db', tmp_path / 'target.db'
    fictional_db(source)
    connection = sqlite3.connect(source)
    try:
        for column, declaration in (
            ('enrollment_hash', 'TEXT'), ('enrollment_expires_at', 'TEXT'),
            ('enrollment_kind', 'TEXT'), ('ical_token', 'TEXT'), ('access_version', 'INTEGER'), ('pin', 'TEXT')):
            connection.execute(f'ALTER TABLE funcionarios ADD COLUMN "{column}" {declaration}')
        connection.execute('UPDATE funcionarios SET enrollment_hash=?, enrollment_expires_at=?, enrollment_kind=?, ical_token=?, access_version=?, pin=?',
            ('fictional-digest', '2099-01-01', 'reset', 'fictional-calendar-token', 2, 'fictional-existing-hash'))
        connection.commit()
    finally:
        connection.close()
    snapshot = Path(backup(source, tmp_path / 'backups', 14))
    snapshot_hash = sha256_file(snapshot)
    monkeypatch.setenv('AMORINO_DB_PATH', str(target))
    restore(snapshot, target, service_stopped=True, confirmed_target=target, allow_repository_target=True)
    with sqlite3.connect(target) as restored:
        row = restored.execute('SELECT enrollment_hash, enrollment_expires_at, enrollment_kind, ical_token, access_version, pin FROM funcionarios').fetchone()
        assert row == ('', None, '', '', 3, 'fictional-existing-hash')
    assert sha256_file(snapshot) == snapshot_hash


def test_restore_live_repository_target_rejected_before_database_access(tmp_path, monkeypatch):
    # No live database is read, including schema/integrity validation.
    live_path = ROOT / 'amorino.db'
    monkeypatch.setenv('AMORINO_DB_PATH', str(live_path))
    with pytest.raises(ValueError, match='live repository'):
        restore(tmp_path / 'not-opened.db', live_path, service_stopped=True,
                confirmed_target=live_path, allow_repository_target=True)


def test_offserver_plan_pins_exact_snapshot_receiver_and_source_checksum(tmp_path):
    source = tmp_path / 'source.db'
    fictional_db(source)
    snapshot = Path(backup(source, tmp_path / 'backups', 14))
    plan = build_plan(tmp_path / 'backups', 'backupuser@backup.example.invalid',
                      'backupuser@backup.example.invalid:/srv/amorino/backups/')
    assert plan['filename'] == snapshot.name
    assert plan['remote_path'] == '/srv/amorino/backups/' + snapshot.name
    assert plan['expected_sha256'] == sha256_file(snapshot)
    verify(snapshot, expected_sha256=plan['expected_sha256'])
    with pytest.raises(ValueError, match='expected source'):
        verify(snapshot, expected_sha256='f' * 64)


@pytest.mark.parametrize('host,destination', [
    ('backupuser@host-a', 'backupuser@host-b:/srv/amorino/backups/'),
    ('backupuser@host-a', 'backupuser@host-a:/different/path/'),
    ('-V', ''), ('backupuser@-V', ''), ('host;echo bad', ''), ('host\nother', ''),
])
def test_offserver_plan_rejects_wrong_receiver_or_shell_characters_before_copy(tmp_path, host, destination):
    with pytest.raises(ValueError):
        build_plan(tmp_path / 'not-opened-backups', host, destination)


def test_backup_prune_does_not_follow_or_count_symlink(tmp_path, monkeypatch):
    oldest = tmp_path / 'amorino-20261006-000000.db'
    newest = tmp_path / 'amorino-20261007-000000.db'
    oldest.write_bytes(b'fictional old backup')
    newest.write_bytes(b'fictional recent backup')
    class FakeLink:
        name = 'amorino-99999999-999999.db'
        path = str(tmp_path / name)
        def is_file(self, *, follow_symlinks=True):
            return follow_symlinks
    entries = list(os.scandir(tmp_path)) + [FakeLink()]
    monkeypatch.setattr('scripts.backup_db.os.scandir', lambda path: entries)
    removed = prune(tmp_path, 1)
    assert removed == [oldest.name]
    assert newest.exists()


def test_backup_failure_reports_nonzero_and_status(tmp_path):
    env = os.environ.copy()
    env['AMORINO_DB_PATH'] = str(tmp_path / 'missing.db')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/backup_db.py'),
        '--dir', str(tmp_path / 'backups')], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads((tmp_path / 'backups/backup-status.json').read_text())['ok'] is False
    assert not (tmp_path / 'missing.db').exists()


def test_complete_fictional_recovery_rehearsal(tmp_path):
    env = os.environ.copy()
    env['AMORINO_DB_PATH'] = str(tmp_path / 'initial-scratch.db')
    report = tmp_path / 'result.json'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/recovery_rehearsal.py'),
        '--work-dir', str(tmp_path / 'rehearsal'), '--report', str(report)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text())
    assert data['ok'] and data['counts']['funcionarios'] == 3
    assert data['counts']['turnos'] == 3 and data['counts']['trocas'] == 1
    assert data['post_snapshot_fictional_changes_lost'] == 1
    assert data['restore_and_application_validation_seconds'] > 0
    assert data['checks']['csv_rows_including_total'] == 4
    assert not data['off_server_copy_verified']
    assert not data['production_https_checked']
