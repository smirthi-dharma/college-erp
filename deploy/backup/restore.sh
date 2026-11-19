#!/usr/bin/env bash
# Restore a backup into a NEW scratch database and check it. This is the monthly restore test (docs/BACKUPS.md).
#
#   download -> verify sha256 -> decrypt -> create a NEW database -> pg_restore -> unpack the uploaded files into a NEW folder
#   -> print what is inside and check that every picture and consent scan the database names is really in the files
#
# It never touches the live database or the live files and never drops or deletes anything: the scratch database name must
# start with "restore_" and must not exist yet, and the files go into a new folder named restore_*. When you are done, remove
# them yourself (the commands are printed at the end).
#
# Usage:  restore.sh [latest | <object name, e.g. erp-20261119T013000Z.dump.gpg>]      (default: latest)
#
# Environment:
#   RESTORE_ADMIN_URL      postgres://user:password@host:port/postgres  a SCRATCH server where you may create databases  # pragma: allowlist secret
#                          (a Postgres on your own machine or a throw-away container; NOT the production server)
#   RESTORE_DATABASE       name of the new database, must match ^restore_[a-z0-9_]+$
#   RESTORE_MEDIA_DIR      absolute path of a NEW (or empty) folder for the uploaded files; its name must start with restore_
#   SKIP_MEDIA=1           optional: restore only the database (for a backup made before uploaded files were included)
#   BACKUP_PASSPHRASE_FILE, BACKUP_DEST, R2_ENDPOINT, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY   as in backup.sh
#   BACKUP_PREFIX          optional: daily (default) or monthly
#   PRODUCTION_DATABASE_URL optional: if set, the restore refuses to run when RESTORE_ADMIN_URL points at the same host
set -euo pipefail

log() { printf '%s restore: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 2; }

: "${RESTORE_ADMIN_URL:?set RESTORE_ADMIN_URL}"
: "${RESTORE_DATABASE:?set RESTORE_DATABASE}"
SKIP_MEDIA="${SKIP_MEDIA:-0}"
if [ "$SKIP_MEDIA" != "1" ]; then : "${RESTORE_MEDIA_DIR:?set RESTORE_MEDIA_DIR (or SKIP_MEDIA=1)}"; fi
: "${BACKUP_PASSPHRASE_FILE:?set BACKUP_PASSPHRASE_FILE}"
: "${BACKUP_DEST:?set BACKUP_DEST}"
which_backup="${1:-latest}"
prefix="${BACKUP_PREFIX:-daily}"

# --- guards: all of these run before anything is downloaded or created -----------------------------------------------
[[ "$RESTORE_DATABASE" =~ ^restore_[a-z0-9_]+$ ]] || die "RESTORE_DATABASE must start with restore_ and use only lowercase letters, digits and _ (got '$RESTORE_DATABASE')"
admin_db="${RESTORE_ADMIN_URL##*/}"; admin_db="${admin_db%%\?*}"
if [ "$SKIP_MEDIA" != "1" ]; then
  [[ "$RESTORE_MEDIA_DIR" = /* ]] || die "RESTORE_MEDIA_DIR must be an absolute path"
  [[ "$(basename "$RESTORE_MEDIA_DIR")" =~ ^restore_[A-Za-z0-9_.-]+$ ]] || die "the last part of RESTORE_MEDIA_DIR must start with restore_ (got '$RESTORE_MEDIA_DIR')"
  [ "$RESTORE_MEDIA_DIR" != "${MEDIA_ROOT:-}" ] || die "RESTORE_MEDIA_DIR is the live MEDIA_ROOT"
  if [ -e "$RESTORE_MEDIA_DIR" ]; then
    [ -d "$RESTORE_MEDIA_DIR" ] && [ -z "$(find "$RESTORE_MEDIA_DIR" -mindepth 1 -maxdepth 1 | head -1)" ] || die "RESTORE_MEDIA_DIR exists and is not empty (nothing is overwritten)"
  fi
fi
[ "$admin_db" != "$RESTORE_DATABASE" ] || die "RESTORE_DATABASE is the database RESTORE_ADMIN_URL connects to"
host_of() { local rest="${1#*://}"; rest="${rest#*@}"; rest="${rest%%/*}"; echo "${rest%%:*}"; }
if [ -n "${PRODUCTION_DATABASE_URL:-}" ] && [ "$(host_of "$RESTORE_ADMIN_URL")" = "$(host_of "$PRODUCTION_DATABASE_URL")" ]; then
  die "RESTORE_ADMIN_URL is on the production database host. Restore on a scratch server."
fi
case "$RESTORE_ADMIN_URL" in *supabase.co*|*pooler*) die "RESTORE_ADMIN_URL looks like Supabase. Restore on a scratch Postgres, not on the live service." ;; esac
[ -r "$BACKUP_PASSPHRASE_FILE" ] || die "cannot read BACKUP_PASSPHRASE_FILE"
case "$BACKUP_DEST" in
  s3://*) : "${R2_ENDPOINT:?set R2_ENDPOINT for an s3:// destination}" ;;
  /*) ;;
  *) die "BACKUP_DEST must be s3://bucket/prefix or an absolute directory" ;;
esac

sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
src="${BACKUP_DEST%/}/${prefix}"

# --- find and fetch ----------------------------------------------------------------------------------------------------
case "$BACKUP_DEST" in
  s3://*)
    if [ "$which_backup" = "latest" ]; then
      which_backup="$(aws s3 ls --endpoint-url "$R2_ENDPOINT" "${src}/" | awk '{print $4}' | grep '\.dump\.gpg$' | sort | tail -1)"
    fi
    [ -n "$which_backup" ] || die "no backups found under ${src}/"
    media_backup="${which_backup%.dump.gpg}.media.tar.gpg"
    log "fetching ${which_backup} and ${media_backup}"
    aws s3 cp --only-show-errors --endpoint-url "$R2_ENDPOINT" "${src}/${which_backup}" "$work/$which_backup"
    aws s3 cp --only-show-errors --endpoint-url "$R2_ENDPOINT" "${src}/${which_backup}.sha256" "$work/$which_backup.sha256"
    if [ "$SKIP_MEDIA" != "1" ]; then
      aws s3 cp --only-show-errors --endpoint-url "$R2_ENDPOINT" "${src}/${media_backup}" "$work/$media_backup"
      aws s3 cp --only-show-errors --endpoint-url "$R2_ENDPOINT" "${src}/${media_backup}.sha256" "$work/$media_backup.sha256"
    fi
    ;;
  *)
    if [ "$which_backup" = "latest" ]; then which_backup="$(find "$src" -maxdepth 1 -name '*.dump.gpg' -exec basename {} \; | sort | tail -1)"; fi
    [ -n "$which_backup" ] || die "no backups found under ${src}/"
    media_backup="${which_backup%.dump.gpg}.media.tar.gpg"
    log "fetching ${which_backup} and ${media_backup}"
    cp "$src/$which_backup" "$work/$which_backup"; cp "$src/$which_backup.sha256" "$work/$which_backup.sha256"
    if [ "$SKIP_MEDIA" != "1" ]; then
      [ -f "$src/$media_backup" ] || die "no uploaded-files archive ${media_backup} next to the database backup (an older backup? use SKIP_MEDIA=1)"
      cp "$src/$media_backup" "$work/$media_backup"; cp "$src/$media_backup.sha256" "$work/$media_backup.sha256"
    fi
    ;;
esac

expected="$(cut -d' ' -f1 "$work/$which_backup.sha256")"
actual="$(sha256_of "$work/$which_backup")"
[ "$expected" = "$actual" ] || die "checksum mismatch: the backup is damaged or was tampered with"
log "checksum ok (database)"
if [ "$SKIP_MEDIA" != "1" ]; then
  [ "$(cut -d' ' -f1 "$work/$media_backup.sha256")" = "$(sha256_of "$work/$media_backup")" ] || die "checksum mismatch on the uploaded-files archive"
  log "checksum ok (uploaded files)"
fi

log "decrypting"
gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
    --decrypt --output "$work/db.dump" "$work/$which_backup" || die "decryption failed: wrong passphrase or damaged file"

# the uploaded-files archive is decrypted and inspected BEFORE anything is created: never trust an archive blindly
if [ "$SKIP_MEDIA" != "1" ]; then
  gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
      --decrypt --output "$work/media.tar" "$work/$media_backup" || die "decryption of the uploaded files failed"
  if tar -tf "$work/media.tar" | grep -Eq '^/|(^|/)\.\.(/|$)'; then die "the uploaded-files archive has unsafe paths (absolute or ..); nothing was created"; fi
fi

# --- restore into a NEW database -----------------------------------------------------------------------------------------
base="${RESTORE_ADMIN_URL%/*}"; query=""; case "$RESTORE_ADMIN_URL" in *\?*) query="?${RESTORE_ADMIN_URL#*\?}" ;; esac
target="${base}/${RESTORE_DATABASE}${query}"
exists="$(psql "$RESTORE_ADMIN_URL" -At -c "select 1 from pg_database where datname = '${RESTORE_DATABASE}'")"
[ -z "$exists" ] || die "database ${RESTORE_DATABASE} already exists; choose a new name (nothing is overwritten)"
log "creating ${RESTORE_DATABASE}"
psql "$RESTORE_ADMIN_URL" -v ON_ERROR_STOP=1 -q -c "create database ${RESTORE_DATABASE}"
started="$(date +%s)"
log "restoring"
pg_restore --exit-on-error --no-owner --no-acl --dbname "$target" "$work/db.dump"
log "restored in $(( $(date +%s) - started )) s"

# --- unpack the uploaded files into the NEW folder (after the database restored) ----------------------------------------
missing=0
if [ "$SKIP_MEDIA" != "1" ]; then
  log "unpacking the uploaded files into ${RESTORE_MEDIA_DIR}"
  mkdir -p "$RESTORE_MEDIA_DIR"
  tar -xf "$work/media.tar" -C "$RESTORE_MEDIA_DIR"
  rm -f "$work/media.tar"
  log "uploaded files restored: $(find "$RESTORE_MEDIA_DIR" -type f | wc -l | tr -d ' ') file(s)"
fi

# --- look inside -----------------------------------------------------------------------------------------------------------
q() { psql "$target" -At -v ON_ERROR_STOP=1 -c "$1"; }
log "colleges: $(q 'select count(*) from colleges_college')"
for schema in $(q "select schema_name from colleges_college where schema_name <> 'public' order by 1"); do
  users="$(q "select count(*) from \"${schema}\".main_app_customuser")"
  audit="$(q "select count(*) from \"${schema}\".audit_trail_auditlog")"
  newest="$(q "select coalesce(max(timestamp)::text, 'none') from \"${schema}\".audit_trail_auditlog")"
  log "  ${schema}: ${users} users, ${audit} audit entries, newest entry ${newest}"
  if [ "$SKIP_MEDIA" != "1" ]; then
    # every picture and every consent-form scan the database names must be a real file in the restored folder
    refs="$(q "select profile_pic from \"${schema}\".main_app_customuser where profile_pic <> '' union all select scan from \"${schema}\".main_app_consentrecord where scan is not null and scan <> ''")"
    total=0; lost=0
    while IFS= read -r ref; do
      [ -n "$ref" ] || continue
      total=$((total + 1))
      if [ ! -f "${RESTORE_MEDIA_DIR}/${schema}/${ref}" ]; then lost=$((lost + 1)); log "  MISSING FILE: ${schema}/${ref}"; fi
    done <<< "$refs"
    log "  ${schema}: ${total} picture/scan reference(s) in the database, ${lost} missing from the restored files"
    missing=$((missing + lost))
  fi
done
if [ "$missing" -gt 0 ]; then
  log "RESTORE INCOMPLETE: ${missing} file(s) the database refers to are not in the backup. Treat this as an incident." >&2
  exit 3
fi
log "RESTORE OK. Record this drill in docs/RESTORE_LOG.md. When finished, remove the scratch copies yourself:"
log "  psql \"\$RESTORE_ADMIN_URL\" -c 'drop database ${RESTORE_DATABASE}'"
[ "$SKIP_MEDIA" = "1" ] || log "  rm -r '${RESTORE_MEDIA_DIR}'"
