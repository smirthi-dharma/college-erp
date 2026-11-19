#!/usr/bin/env bash
# Nightly backup of the whole database (every college's schema) to Cloudflare R2, encrypted before it leaves the server.
#
#   pg_dump (custom format) -> checks the dump is readable -> gpg AES-256 -> sha256 -> upload -> verify the upload -> ping
#
# Nothing is ever deleted here. Old copies expire by R2 lifecycle rules (see docs/BACKUPS.md).
#
# Environment (all required unless marked optional; secrets come from the environment or a file, never from this script):
#   DATABASE_URL           postgres://user:password@host:5432/dbname  (Supabase SESSION pooler or direct, never port 6543)  # pragma: allowlist secret
#   BACKUP_PASSPHRASE_FILE path of a file that holds the encryption passphrase (mode 600). Keep a copy OFF the server.
#   BACKUP_DEST            s3://bucket/prefix   (Cloudflare R2)   or an absolute directory (local test only)
#   R2_ENDPOINT            https://<account id>.r2.cloudflarestorage.com   (needed for s3:// destinations)
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY   an R2 API token limited to this bucket
#   BACKUP_PING_URL        optional: a dead-man's-switch URL. Called on success; "<url>/fail" is called on failure.
#   DRY_RUN=1              print what would happen and change nothing
set -euo pipefail

log() { printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 2; }

: "${DATABASE_URL:?set DATABASE_URL}"
: "${BACKUP_PASSPHRASE_FILE:?set BACKUP_PASSPHRASE_FILE}"
: "${BACKUP_DEST:?set BACKUP_DEST}"
DRY_RUN="${DRY_RUN:-0}"
case "$DATABASE_URL" in *:6543/*) die "DATABASE_URL uses port 6543 (the transaction pooler); pg_dump needs the session pooler or a direct connection." ;; esac
[ -r "$BACKUP_PASSPHRASE_FILE" ] || die "cannot read BACKUP_PASSPHRASE_FILE"
[ -s "$BACKUP_PASSPHRASE_FILE" ] || die "BACKUP_PASSPHRASE_FILE is empty"
case "$BACKUP_DEST" in
  s3://*) : "${R2_ENDPOINT:?set R2_ENDPOINT for an s3:// destination}" ;;
  /*) ;;
  *) die "BACKUP_DEST must be s3://bucket/prefix or an absolute directory" ;;
esac

sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
size_of() { wc -c < "$1" | tr -d ' '; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="erp-${stamp}.dump.gpg"
prefixes="daily"
[ "$(date -u +%d)" = "01" ] && prefixes="daily monthly"  # the first of the month is also kept as a monthly copy

if [ "$DRY_RUN" = "1" ]; then
  log "DRY RUN: would pg_dump --format=custom the database, check the dump is readable and lists colleges_college"
  log "DRY RUN: would encrypt it with gpg AES256 into ${name} and write ${name}.sha256"
  for p in $prefixes; do log "DRY RUN: would upload both files to ${BACKUP_DEST%/}/${p}/ and compare the stored size"; done
  log "DRY RUN: would ping BACKUP_PING_URL on success (set: $([ -n "${BACKUP_PING_URL:-}" ] && echo yes || echo no))"
  exit 0
fi

work="$(mktemp -d)"
ping() { [ -n "${BACKUP_PING_URL:-}" ] && curl -fsS -m 15 --retry 3 -o /dev/null "${BACKUP_PING_URL}${1:-}" || true; }
cleanup() { status=$?; rm -rf "$work"; [ "$status" -ne 0 ] && { log "FAILED (exit $status)" >&2; ping /fail; }; exit "$status"; }
trap cleanup EXIT

log "dumping the database"
pg_dump --format=custom --compress=9 --no-owner --no-acl --file "$work/db.dump" "$DATABASE_URL"
[ "$(size_of "$work/db.dump")" -gt 1024 ] || die "the dump is suspiciously small"
pg_restore --list "$work/db.dump" > "$work/toc.txt" || die "the dump cannot be read back"
grep -q ' colleges_college ' "$work/toc.txt" || die "the dump does not contain colleges_college: wrong database?"
log "dump ok ($(size_of "$work/db.dump") bytes, $(grep -c . "$work/toc.txt") objects)"

log "encrypting"
gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
    --symmetric --cipher-algo AES256 --compress-algo none --output "$work/$name" "$work/db.dump"
rm -f "$work/db.dump" "$work/toc.txt"  # the readable copy never stays on disk
sha="$(sha256_of "$work/$name")"
printf '%s  %s\n' "$sha" "$name" > "$work/$name.sha256"
want="$(size_of "$work/$name")"

for p in $prefixes; do
  dest="${BACKUP_DEST%/}/${p}"
  log "uploading to ${dest}/"
  case "$BACKUP_DEST" in
    s3://*)
      aws s3 cp --only-show-errors --endpoint-url "$R2_ENDPOINT" "$work/$name" "${dest}/${name}"
      aws s3 cp --only-show-errors --endpoint-url "$R2_ENDPOINT" "$work/$name.sha256" "${dest}/${name}.sha256"
      got="$(aws s3api head-object --endpoint-url "$R2_ENDPOINT" --bucket "$(echo "$dest" | cut -d/ -f3)" \
             --key "$(echo "$dest" | cut -d/ -f4-)/${name}" --query ContentLength --output text)"
      ;;
    *)
      mkdir -p "$dest"
      cp "$work/$name" "$dest/$name"
      cp "$work/$name.sha256" "$dest/$name.sha256"
      got="$(size_of "$dest/$name")"
      ;;
  esac
  [ "$got" = "$want" ] || die "the stored copy has ${got} bytes, expected ${want}"
done

log "backup ${name} done (sha256 ${sha})"
ping
