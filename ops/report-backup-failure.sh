#!/bin/sh
set -eu
date -u '+%Y-%m-%dT%H:%M:%SZ backup or off-server copy failed' > /var/lib/amorino/backup-failure
logger -p daemon.err -t amorino 'Daily backup/off-server verification failed. Check amorino-backup.service; operator action required.'
# Wire an approved monitor to this marker and systemd failure state. A journal
# entry alone does not notify a human. Do not put employee records in alerts.
