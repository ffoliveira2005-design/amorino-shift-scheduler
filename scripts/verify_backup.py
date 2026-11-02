"""Validate a copied backup or detect a missing/stale daily snapshot."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import re

try:
    from .ops_common import validate_database, verify_manifest
except ImportError:
    from ops_common import validate_database, verify_manifest


def verify(path, max_age_hours=None, expected_sha256=None):
    manifest = verify_manifest(path, require_manifest=True)
    if expected_sha256 is not None:
        if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256) or manifest['sha256'] != expected_sha256:
            raise ValueError('Backup does not match the expected source SHA-256')
    validate_database(path)
    stamp = datetime.fromisoformat(manifest['snapshot_started_utc'])
    age = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
    if age < -0.1:
        raise ValueError('Backup timestamp is in the future; check clock synchronisation')
    if max_age_hours is not None and age > max_age_hours:
        raise ValueError(f'Backup is older than {max_age_hours} hours')
    return age


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--backup')
    group.add_argument('--dir', help='check the newest completed database + manifest in this directory')
    parser.add_argument('--max-age-hours', type=float)
    parser.add_argument('--expected-sha256', help='verify copied bytes against the independent source checksum')
    args = parser.parse_args()
    try:
        path = Path(args.backup) if args.backup else next(iter(sorted(
            Path(args.dir).glob('amorino-*.db'), reverse=True)), None)
        if path is None:
            raise ValueError('No completed backup exists')
        age = verify(path, args.max_age_hours, args.expected_sha256)
        print(f'PASS {path.name}; snapshot age {age:.2f} hours; integrity, schema and SHA-256 verified.')
        return 0
    except Exception as exc:
        print(f'Backup verification failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
