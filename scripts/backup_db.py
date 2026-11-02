"""Take a consistent backup of amorino.db while the app is running.

The deployment checklist has said "regular backups" for a while without anything
in the repo that makes one, so the step depended on someone remembering a
command. This is that command.

    python scripts/backup_db.py                 # -> backups/amorino-<stamp>.db
    python scripts/backup_db.py --dir D:/copias --keep 30

Why not `copy amorino.db backup.db`: a plain file copy of a live SQLite database
can capture a half-written transaction, and in WAL mode it silently misses every
commit still sitting in the -wal file. The result looks like a valid file and
fails when you need it. sqlite3's online backup API walks the database through
its own locking instead, so the copy is a real point-in-time snapshot taken
without stopping the app.

Exits non-zero on failure so a scheduled task can alert on it.
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
import secrets

try:
    from .ops_common import atomic_json, readonly, sha256_file, validate_database
except ImportError:
    from ops_common import atomic_json, readonly, sha256_file, validate_database

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, 'amorino.db')
DEFAULT_DIR = os.path.join(ROOT, 'backups')


def prune(directory: str, keep: int) -> list:
    """Delete all but the newest `keep` backups. Returns what was removed."""
    if keep <= 0:
        return []
    entries = sorted(
        (e for e in os.scandir(directory)
         if e.is_file(follow_symlinks=False) and e.name.startswith('amorino-') and e.name.endswith('.db')),
        key=lambda e: e.name, reverse=True,
    )
    removed = []
    for stale in entries[keep:]:
        os.remove(stale.path)
        Path(stale.path + '.json').unlink(missing_ok=True)
        removed.append(stale.name)
    return removed


def backup(db_path: str, out_dir: str, keep: int) -> str:
    if not os.path.exists(db_path):
        raise SystemExit(f'No database at {db_path}')
    os.makedirs(out_dir, exist_ok=True)
    started = datetime.now(timezone.utc)
    stamp = started.strftime('%Y%m%d-%H%M%S-%f') + '-' + secrets.token_hex(3)
    dest = os.path.join(out_dir, f'amorino-{stamp}.db')
    temporary = dest + '.partial'
    try:
        with readonly(db_path) as src:
            dst = sqlite3.connect(temporary)
            try:
                src.backup(dst)
            finally:
                dst.close()
        counts = validate_database(temporary, {'funcionarios', 'turnos'})
        with open(temporary, 'r+b') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, dest)
        completed = datetime.now(timezone.utc)
        atomic_json(dest + '.json', {
            'format': 1, 'snapshot_started_utc': started.isoformat(),
            'completed_utc': completed.isoformat(), 'sha256': sha256_file(dest),
            'bytes': os.path.getsize(dest), 'counts': counts,
            'method': 'sqlite-online-backup',
        })
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

    size = os.path.getsize(dest) / 1024
    print(f'{dest}  ({size:.0f} KB, {counts["funcionarios"]} funcionarios, {counts["turnos"]} turnos)')
    for name in prune(out_dir, keep):
        print(f'  removed old backup {name}')
    return dest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--db', default=os.environ.get('AMORINO_DB_PATH') or DEFAULT_DB,
                   help='database to back up (default: the live amorino.db)')
    p.add_argument('--dir', default=DEFAULT_DIR, dest='out_dir',
                   help='where to write backups (default: ./backups)')
    p.add_argument('--keep', type=int, default=14,
                   help='how many backups to retain, 0 to keep all (default: 14)')
    args = p.parse_args()
    try:
        dest = backup(args.db, args.out_dir, args.keep)
        atomic_json(Path(args.out_dir) / 'backup-status.json', {
            'ok': True, 'checked_utc': datetime.now(timezone.utc).isoformat(),
            'backup': Path(dest).name,
        })
    except (Exception, SystemExit) as exc:
        atomic_json(Path(args.out_dir) / 'backup-status.json', {
            'ok': False, 'checked_utc': datetime.now(timezone.utc).isoformat(),
            'error': type(exc).__name__,
        })
        print('Backup failed; inspect the local job log. No successful backup was reported.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
