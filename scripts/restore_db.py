"""Guarded offline restore; preserves the damaged database and every sidecar."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sqlite3
import sys

try:
    from .ops_common import ROOT, atomic_json, readonly, sha256_file, validate_database, verify_manifest
except ImportError:
    from ops_common import ROOT, atomic_json, readonly, sha256_file, validate_database, verify_manifest


def restore(snapshot, target, *, service_stopped=False, confirmed_target=None,
            allow_repository_target=False, require_manifest=True):
    """Caller must stop all app processes first. Flags are operator attestations."""
    if not Path(target).is_absolute():
        raise ValueError('--target must be an absolute path.')
    raw_target = Path(target).absolute()
    target = raw_target.resolve()
    snapshot = Path(snapshot).resolve()
    if not service_stopped:
        raise ValueError('Stop the service and all writers, then pass --service-stopped.')
    if not confirmed_target or not Path(confirmed_target).is_absolute() or Path(confirmed_target).resolve() != target:
        raise ValueError('--confirm-target must repeat the exact absolute restore target.')
    if not os.environ.get('AMORINO_DB_PATH') or not Path(os.environ['AMORINO_DB_PATH']).is_absolute() or Path(os.environ['AMORINO_DB_PATH']).resolve() != target:
        raise ValueError('Set AMORINO_DB_PATH explicitly to the restore target.')
    if target != raw_target or (target.exists() and target.is_symlink()):
        raise ValueError('Redirected restore paths are unsupported.')
    if target == snapshot:
        raise ValueError('The snapshot and target must be different files.')
    if target == ROOT / 'amorino.db':
        raise ValueError('Restoration over the live repository amorino.db is prohibited; use a separate approved data path.')
    if target.is_relative_to(ROOT) and not allow_repository_target:
        raise ValueError('Source-checkout restores need --allow-repository-target; prefer separate production data.')
    sidecars = [Path(str(target) + suffix) for suffix in ('', '-wal', '-shm', '-journal')]
    if any(path.exists() and (path.is_symlink() or path.resolve() != path) for path in sidecars):
        raise ValueError('Redirected database or sidecar paths are unsupported.')
    validate_database(snapshot)
    backup_manifest = verify_manifest(snapshot, require_manifest=require_manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + '-' + secrets.token_hex(3)
    staged = target.with_name(target.name + '.restore-' + stamp)
    marker = target.with_name(target.name + '.restore-in-progress')
    if marker.exists():
        raise ValueError('An earlier restore is incomplete; inspect its preservation directory before proceeding.')
    preserved = target.parent / ('damaged-' + stamp)
    try:
        with readonly(snapshot) as source:
            destination = sqlite3.connect(staged)
            try:
                source.backup(destination)
                # A snapshot may contain invitations/calendar links already
                # revoked or consumed since it was taken. Never resurrect them.
                columns = {row[1] for row in destination.execute('PRAGMA table_info(funcionarios)')}
                revoke = {'enrollment_hash': "''", 'enrollment_expires_at': 'NULL',
                          'enrollment_kind': "''", 'ical_token': "''",
                          'access_version': 'COALESCE(access_version, 0) + 1'}
                assignments = [f'"{column}" = {expression}' for column, expression in revoke.items() if column in columns]
                if assignments:
                    destination.execute('UPDATE funcionarios SET ' + ', '.join(assignments))
                audit_columns = {row[1] for row in destination.execute('PRAGMA table_info(audit_log)')}
                if {'ts', 'actor', 'ip', 'action', 'target', 'detail'} <= audit_columns:
                    destination.execute('INSERT INTO audit_log (ts, actor, ip, action, target, detail) VALUES (?, ?, ?, ?, ?, ?)',
                        (datetime.now(timezone.utc).isoformat(), 'OPERATOR', '', 'restore_revoke_access',
                         'all_employees', 'Recovery revoked enrollment/calendar secrets and advanced session versions; rotate session signing secret before reopening.'))
                destination.commit()
            finally:
                destination.close()
        counts = validate_database(staged)
        with staged.open('r+b') as handle:
            os.fsync(handle.fileno())
        preserved.mkdir()
        preservation = {'created_utc': datetime.now(timezone.utc).isoformat(),
                        'target': str(target), 'snapshot': snapshot.name,
                        'snapshot_sha256': sha256_file(snapshot), 'files': {},
                        'snapshot_started_utc': (backup_manifest or {}).get('snapshot_started_utc')}
        atomic_json(marker, {'preserved': str(preserved), 'staged': str(staged)})
        for path in sidecars:
            if path.exists():
                # Hash before move; a failed restore leaves the originals recoverable.
                preservation['files'][path.name] = sha256_file(path)
                os.replace(path, preserved / path.name)
                atomic_json(preserved / 'preservation.json', preservation)
        atomic_json(preserved / 'preservation.json', preservation)
        os.replace(staged, target)
        validate_database(target)
        atomic_json(target.with_name(target.name + '.restore-report.json'), {
            'ok': True, 'restored_utc': datetime.now(timezone.utc).isoformat(),
            'preserved': str(preserved), 'snapshot': snapshot.name, 'counts': counts,
            'application_validation': 'pending; run isolated rehearsal / post-restore operator checks',
            'security_reconciliation': 'invitations/calendar secrets revoked; rotate AMORINO_SECRET_KEY and reapply lost PIN resets before reopening',
        })
        marker.unlink()
        return {'database': str(target), 'preserved': str(preserved), 'counts': counts}
    finally:
        staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--confirm-target', required=True)
    parser.add_argument('--service-stopped', action='store_true')
    parser.add_argument('--allow-repository-target', action='store_true')
    parser.add_argument('--allow-without-manifest', action='store_true', help='legacy snapshots only; still verifies integrity/schema')
    args = parser.parse_args()
    try:
        result = restore(args.backup, args.target, service_stopped=args.service_stopped,
                         confirmed_target=args.confirm_target,
                         allow_repository_target=args.allow_repository_target,
                         require_manifest=not args.allow_without_manifest)
    except Exception as exc:
        print(f'Restore failed: {exc}', file=sys.stderr)
        return 1
    print(f'Restored {result["database"]}; damaged files preserved at {result["preserved"]}.')
    print('Keep employee access restricted until login, roster, published schedule, swaps and exports have been checked.')
    print('Rotate AMORINO_SECRET_KEY and reconcile resets since the snapshot before reopening. Staff must recreate calendar subscriptions.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
