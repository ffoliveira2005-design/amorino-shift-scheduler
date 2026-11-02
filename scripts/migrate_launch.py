"""Back up and rehearse launch migrations on a copy; apply only offline on confirmation.

Always requires an absolute --db matching AMORINO_DB_PATH. The repository's
live amorino.db is prohibited even for read-only rehearsal. No source seed data
is inserted. Reports contain counts and preservation checks, never credentials.
"""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time

try:
    from .backup_db import backup
    from .ops_common import ROOT, atomic_json, readonly, validate_database, verify_manifest
except ImportError:
    from backup_db import backup
    from ops_common import ROOT, atomic_json, readonly, validate_database, verify_manifest

AUTH_COLUMNS = {'access_version', 'enrollment_hash', 'enrollment_expires_at', 'enrollment_kind'}
PUBLICATION_TABLES = {'published_turnos', 'published_descargas', 'published_semanas',
                      'schedule_releases', 'schedule_changes', 'schema_migrations'}
MIGRATION_NAME = 'schedule_publication_v1'


def _validated_database(value):
    raw = Path(value)
    if not raw.is_absolute():
        raise ValueError('--db must be an absolute database path.')
    database = raw.resolve()
    if database == (ROOT / 'amorino.db').resolve():
        raise ValueError('The live repository amorino.db is prohibited; use an approved external data path.')
    if database != raw.absolute() or (raw.exists() and raw.is_symlink()):
        raise ValueError('Redirected database paths are unsupported.')
    if str(value).startswith(('\\\\', '//')):
        raise ValueError('A network-share database is unsupported.')
    configured = os.environ.get('AMORINO_DB_PATH', '')
    if not configured or not Path(configured).is_absolute() or Path(configured).resolve() != database:
        raise ValueError('Set AMORINO_DB_PATH explicitly to the same absolute --db path.')
    if not database.is_file():
        raise ValueError('The source database must already exist; this tool never creates an empty source.')
    return database


def _rows(connection, table):
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]


def inspect(database):
    """Sensitive comparisons stay in memory; callers must not serialize this dict."""
    counts = validate_database(database)
    with readonly(database) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        people = _rows(conn, 'funcionarios')
        weeks = _rows(conn, 'semanas')
        availability = []
        for row in _rows(conn, 'disponibilidades'):
            # Match already documented legacy normalisations without dropping
            # availability, delivery opt-in or preferences.
            availability.append((row['id'], row['semana'], row['funcionario'], row['dia'],
                '' if row['tipo'] == 'extra' else row['tipo'], row.get('texto') or '',
                row.get('lojas_pref') or '', (row.get('slots_pref') or '').replace('fecho', 'noite'),
                bool(row.get('descarga_opt')) or row['tipo'] == 'extra',
                row.get('submetido_em'), bool(row.get('revisto'))))
        classification = {'past_current': [], 'future': [], 'undated': []}
        for week in weeks:
            days = json.loads(week.get('dias_json') or '[]')
            if not isinstance(days, list):
                raise ValueError('Week date metadata must be a JSON list; repair an isolated copy first.')
            dates = [day.get('date') for day in days if day.get('date')]
            for value in dates:
                date.fromisoformat(value)
            category = ('undated' if not dates else 'past_current'
                        if min(dates) <= date.today().isoformat() else 'future')
            classification[category].append(week['codigo'])
        releases = (_rows(conn, 'schedule_releases') if 'schedule_releases' in tables else [])
        projection_ok = True
        for code in {row['semana'] for row in releases}:
            latest = max((row for row in releases if row['semana'] == code), key=lambda row: row['version'])
            snapshot = json.loads(latest['snapshot_json'])
            for table, component in (('published_turnos', 'shifts'), ('published_descargas', 'deliveries')):
                if table not in tables:
                    projection_ok = False
                    continue
                actual = [row for row in _rows(conn, table) if row['semana'] == code]
                expected = snapshot[component]
                fields = (('id', 'semana', 'dia', 'loja', 'horario', 'funcionario', 'nota')
                          if component == 'shifts' else
                          ('id', 'semana', 'dia', 'loja', 'horario', 'funcionario'))
                projection_ok = projection_ok and (
                    sorted(tuple(row.get(key) for key in fields) for row in actual) ==
                    sorted(tuple(row.get(key) for key in fields) for row in expected) and
                    all(row['version'] == latest['version'] for row in actual))
            published_week = ([row for row in _rows(conn, 'published_semanas') if row['codigo'] == code]
                              if 'published_semanas' in tables else [])
            projection_ok = projection_ok and len(published_week) == 1 and (
                published_week[0]['version'] == latest['version'] and
                all(published_week[0].get(key) == value for key, value in snapshot['week'].items()))
        marker = ('schema_migrations' in tables and
                  conn.execute('SELECT 1 FROM schema_migrations WHERE name=?', (MIGRATION_NAME,)).fetchone() is not None)
        return {
            'counts': counts, 'tables': tables,
            'auth_columns': {row[1] for row in conn.execute('PRAGMA table_info(funcionarios)')},
            'credentials': sorted((row['sigla'], row.get('pin') or '') for row in people),
            'blank_accounts': sum(not row.get('pin') for row in people),
            'active_weeks': sorted((row['codigo'], bool(row['ativa'])) for row in weeks),
            'availability': sorted(availability),
            'shifts': sorted(tuple(row.get(key) or '' for key in
                ('id', 'semana', 'dia', 'loja', 'horario', 'funcionario', 'nota'))
                for row in _rows(conn, 'turnos')),
            'deliveries': sorted(tuple(row.get(key) or '' for key in
                ('id', 'semana', 'dia', 'loja', 'horario', 'funcionario'))
                for row in _rows(conn, 'descargas')),
            'classification': {key: sorted(value) for key, value in classification.items()},
            'release_versions': sorted((row['semana'], row['version'], row['actor']) for row in releases),
            'release_content': sorted((row['semana'], row['version'], row['snapshot_json'],
                                      row['changes_json']) for row in releases),
            'migration_applied': marker,
            'projections_consistent': projection_ok,
        }


def _boot(database):
    env = dict(os.environ, AMORINO_DB_PATH=str(database), AMORINO_SEED='0',
               AMORINO_SECRET_KEY=secrets.token_hex(32),
               AMORINO_SUPERVISOR_PIN='migration-' + secrets.token_hex(16),
               AMORINO_HTTPS='0', AMORINO_TRUST_PROXY='0', AMORINO_DEBUG='0',
               AMORINO_DEMO='0', TZ='Europe/Lisbon')
    code = ('import sys; sys.path.insert(0, sys.argv[1]); '
            'from scripts.migrate_launch import _validated_database; '
            '_validated_database(sys.argv[2]); import app; '
            'app.app.app_context().push(); app.db.session.remove(); app.db.engine.dispose()')
    result = subprocess.run([sys.executable, '-c', code, str(ROOT), str(database)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Application migration failed. Preserve the target and verified backup; keep the service stopped.')


def _preservation_checks(before, after):
    fields = ('credentials', 'active_weeks', 'availability', 'shifts', 'deliveries', 'counts')
    checks = {field + '_preserved': before[field] == after[field] for field in fields}
    checks['additive_auth_schema_present'] = AUTH_COLUMNS <= after['auth_columns']
    checks['publication_schema_present'] = PUBLICATION_TABLES <= after['tables']
    checks['migration_marker_present'] = after['migration_applied']
    checks['authorised_projections_match_latest_release'] = after['projections_consistent']
    if not all(checks.values()):
        raise AssertionError('Migration preservation check failed: ' + ', '.join(key for key, ok in checks.items() if not ok))
    if not before['migration_applied']:
        old_codes = {row[0] for row in before['release_versions']}
        published = {row[0] for row in after['release_versions']}
        expected = old_codes | set(before['classification']['past_current'])
        if published != expected:
            raise AssertionError('Historical/current/future publication policy did not match the verified input.')
        if any(version != 1 or actor != 'MIGRATION' for code, version, actor in
               after['release_versions'] if code not in old_codes):
            raise AssertionError('Initial migrated releases must record version 1 and actor MIGRATION.')
    checks['historical_current_future_policy_verified'] = True
    return checks


def run(database, *, apply=False, service_stopped=False, confirmed_target=None,
        work_directory=None, backup_directory=None, report_path=None):
    source = _validated_database(database)  # refuse root DB before any read
    if apply:
        if not service_stopped:
            raise ValueError('Stop the service and all writers, then pass --service-stopped.')
        if (not confirmed_target or not Path(confirmed_target).is_absolute()
                or Path(confirmed_target).absolute() != source):
            raise ValueError('--confirm-target must repeat the exact absolute --db path.')
        if not backup_directory or not Path(backup_directory).is_absolute():
            raise ValueError('Applying requires an absolute --backup-dir for the verified rollback snapshot.')
    work = Path(work_directory or tempfile.mkdtemp(prefix='amorino-migration-')).absolute()
    if work.resolve() != work or work == ROOT or work == source.parent:
        raise ValueError('Use a dedicated non-redirected migration work directory.')
    if work.exists() and any(work.iterdir()):
        raise ValueError('The migration work directory must be empty.')
    output = Path(report_path or work / 'migration-result.json').absolute()
    if output.resolve() != output or output.suffix.lower() != '.json' or output == source:
        raise ValueError('Use a non-redirected .json path for the migration report.')
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    validate_database(source)
    snapshot = Path(backup(str(source), str(backup_directory or work / 'backups'), keep=0))
    verify_manifest(snapshot, require_manifest=True)
    # The backup is the comparison boundary. A read-only rehearsal may run
    # while its source serves users; compare the consistent snapshot, not
    # different moments of a source that another process may be updating.
    before = inspect(snapshot)
    target = source if apply else work / 'migration-copy.db'
    if not apply:
        with readonly(snapshot) as src:
            connection = sqlite3.connect(target)
            try:
                src.backup(connection)
            finally:
                connection.close()
    marker = target.with_name(target.name + '.migration-in-progress')
    if marker.exists():
        raise ValueError('An earlier migration is incomplete; inspect its rollback snapshot before proceeding.')
    atomic_json(marker, {'started_utc': datetime.now(timezone.utc).isoformat(),
                         'backup': str(snapshot), 'mode': 'apply' if apply else 'rehearsal'})
    _boot(target)
    after = inspect(target)
    checks = _preservation_checks(before, after)
    _boot(target)
    repeated = inspect(target)
    checks['second_boot_idempotent'] = (after['release_content'] == repeated['release_content']
        and after['auth_columns'] == repeated['auth_columns'] and
        after['availability'] == repeated['availability'] and after['credentials'] == repeated['credentials'])
    if not checks['second_boot_idempotent']:
        raise AssertionError('A second application import changed migrated releases or preserved data.')
    checks['integrity_check'] = True
    result = {
        'ok': True, 'recorded_utc': datetime.now(timezone.utc).isoformat(),
        'mode': 'apply' if apply else 'scratch-copy-rehearsal',
        'source_database': str(source), 'migration_database': str(target),
        'source_migrated': apply, 'seed_disabled': True, 'backup': str(snapshot),
        'backup_manifest_verified': True, 'duration_seconds': round(time.perf_counter() - started, 3),
        'effective_local_date': date.today().isoformat(), 'counts_before': before['counts'],
        'counts_after': after['counts'], 'weeks': before['classification'],
        'releases': [{'week': code, 'version': version, 'actor': actor}
                     for code, version, actor in after['release_versions']],
        'blank_accounts_preserved_locked': after['blank_accounts'], 'checks': checks,
        'production_deployment_verified': False,
        'rollback': 'Keep writers stopped; preserve failed database and sidecars, restore the verified pre-migration snapshot using restore_db.py, and restore the exact archived pre-change source/configuration. Never guess a Git commit when work was uncommitted.',
    }
    atomic_json(output, result)
    marker.unlink()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--service-stopped', action='store_true')
    parser.add_argument('--confirm-target')
    parser.add_argument('--backup-dir')
    parser.add_argument('--work-dir')
    parser.add_argument('--report')
    args = parser.parse_args()
    try:
        result = run(args.db, apply=args.apply, service_stopped=args.service_stopped,
                     confirmed_target=args.confirm_target, work_directory=args.work_dir,
                     backup_directory=args.backup_dir, report_path=args.report)
    except Exception as exc:
        print(f'Migration stopped: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
