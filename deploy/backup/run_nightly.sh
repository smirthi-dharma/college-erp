#!/usr/bin/env bash
# Runs backup.sh every night at 02:00 India time (20:30 UTC) from inside the `backup` container. A failed run is reported by
# backup.sh itself (it pings <BACKUP_PING_URL>/fail) and simply tried again the next night; this loop never exits on its own.
#
#   run_nightly.sh                  wait for the next 20:30 UTC, back up, repeat forever (what the container does)
#   run_nightly.sh --once           back up now, once, then stop (the first run, by hand, from the Coolify terminal)
#   run_nightly.sh --seconds-until  print how many seconds until the next run (used by the tests); FAKE_NOW=<ISO time> pretends it is that time
#
# Environment: everything backup.sh needs (see that file), except that the passphrase comes as BACKUP_PASSPHRASE (a container
# environment variable) and is written to a private temporary file, because backup.sh reads it from a file.
# BACKUP_RUN_AT_UTC (default 20:30) changes the time.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_AT_UTC="${BACKUP_RUN_AT_UTC:-20:30}"
log() { printf '%s run_nightly: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

seconds_until() {
  python3 - "$RUN_AT_UTC" <<'PY'
import datetime, os, sys
hour, minute = (int(part) for part in sys.argv[1].split(':'))
fake = os.environ.get('FAKE_NOW')
now = datetime.datetime.fromisoformat(fake.replace('Z', '+00:00')) if fake else datetime.datetime.now(datetime.timezone.utc)
target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
if target <= now:
    target += datetime.timedelta(days=1)
print(int((target - now).total_seconds()))
PY
}

if [ "${1:-}" = "--seconds-until" ]; then seconds_until; exit 0; fi

: "${BACKUP_PASSPHRASE:?set BACKUP_PASSPHRASE}"
umask 077
pass_file="$(mktemp "${TMPDIR:-/tmp}/backup-pass.XXXXXX")"
trap 'rm -f "$pass_file"' EXIT
printf '%s' "$BACKUP_PASSPHRASE" > "$pass_file"
unset BACKUP_PASSPHRASE   # the child process gets the file path, not the secret itself
export BACKUP_PASSPHRASE_FILE="$pass_file"

if [ "${1:-}" = "--once" ]; then
  status=0
  "$here/backup.sh" || status=$?   # not exec: the trap must still run so the passphrase file is removed
  exit "$status"
fi

log "started; the next backup is in $(seconds_until) s (at ${RUN_AT_UTC} UTC)"
while true; do
  sleep "$(seconds_until)"
  "$here/backup.sh" || log "the backup failed (backup.sh has reported it); trying again tomorrow"
  sleep 90   # never run twice inside the same minute
done
