#!/bin/sh
# Copy verified snapshots over SSH, then validate on the receiver. Not executed
# by preparation/rehearsal. A missing destination intentionally fails the job.
set -eu
: "${AMORINO_OFFSERVER_DEST:?Company-owned off-server destination is pending}"
: "${AMORINO_OFFSERVER_HOST:?Company-owned off-server SSH host is pending}"
PYTHON=/opt/amorino/venv/bin/python
BACKUPS=/var/lib/amorino/backups
plan=$("$PYTHON" /opt/amorino/app/scripts/offserver_plan.py --dir "$BACKUPS" --format line)
snapshot=${plan%% *}
expected_sha256=${plan#* }
# No --delete: receiver retention is separately approved and configured.
rsync -a --protect-args "$BACKUPS/$snapshot" "$BACKUPS/$snapshot.json" "$AMORINO_OFFSERVER_DEST"
# Install the two stdlib-only scripts on the receiver; fixed paths avoid an
# arbitrary remote shell command originating in configuration.
ssh -o BatchMode=yes "$AMORINO_OFFSERVER_HOST" "python3 /opt/amorino-recovery/scripts/verify_backup.py --backup /srv/amorino/backups/$snapshot --expected-sha256 $expected_sha256 --max-age-hours 26"
date -u '+%Y-%m-%dT%H:%M:%SZ off-server SHA-256/schema/integrity verification passed' > /var/lib/amorino/offserver-status
