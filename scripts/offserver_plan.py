"""Validate one exact backup and its approved SSH receiver; never connects remotely."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

try:
    from .ops_common import sha256_file
    from .verify_backup import verify
except ImportError:
    from ops_common import sha256_file
    from verify_backup import verify

HOST_PATTERN = r'(?:[A-Za-z0-9][A-Za-z0-9._-]*@)?[A-Za-z0-9][A-Za-z0-9._-]*'
BACKUP_PATTERN = r'amorino-[0-9]{8}-[0-9]{6}(?:-[0-9]{6}-[a-f0-9]{6})?\.db'
RECEIVER_DIRECTORY = '/srv/amorino/backups/'


def build_plan(directory, host, destination=None, max_age_hours=26):
    if not re.fullmatch(HOST_PATTERN, host or ''):
        raise ValueError('Specify an approved SSH hostname/user with no options or shell characters.')
    expected_destination = host + ':' + RECEIVER_DIRECTORY
    if destination and destination != expected_destination:
        raise ValueError('Upload destination and verification host/directory must be identical.')
    directory = Path(directory).absolute()
    if directory.resolve() != directory:
        raise ValueError('Redirected backup directories are unsupported.')
    snapshots = sorted((path for path in directory.glob('amorino-*.db')
                        if path.is_file() and not path.is_symlink()), reverse=True)
    if not snapshots:
        raise ValueError('No completed local backup exists.')
    snapshot = snapshots[0]
    if not re.fullmatch(BACKUP_PATTERN, snapshot.name):
        raise ValueError('Backup filename is outside the safe generated naming scheme.')
    verify(snapshot, max_age_hours)
    return {'host': host, 'destination': expected_destination, 'filename': snapshot.name,
            'source_path': str(snapshot), 'expected_sha256': sha256_file(snapshot),
            'remote_path': RECEIVER_DIRECTORY + snapshot.name}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', required=True)
    parser.add_argument('--host', default=os.environ.get('AMORINO_OFFSERVER_HOST', ''))
    parser.add_argument('--destination', default=os.environ.get('AMORINO_OFFSERVER_DEST', ''))
    parser.add_argument('--format', choices=('json', 'line'), default='json')
    args = parser.parse_args()
    try:
        plan = build_plan(args.dir, args.host, args.destination)
        if args.format == 'line':
            # Both fields have strict safe alphabets; the shell parses this
            # data without eval, command substitution or code generation.
            print(plan['filename'] + ' ' + plan['expected_sha256'])
        else:
            print(json.dumps(plan))
        return 0
    except Exception as exc:
        print(f'Off-server preparation failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
