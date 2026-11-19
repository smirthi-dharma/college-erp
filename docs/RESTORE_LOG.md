# Restore test log

One line per drill (`docs/BACKUPS.md`, section 3). The monthly drill is a non-negotiable: a backup that has never been restored
is not a backup.

| Date | Backup file | Done by | Time to restore | Newest audit entry in it | Result | Notes |
|---|---|---|---|---|---|---|
| 2026-11-19 | local test folder, development database | automated (local drill, not R2, not Supabase) | under 1 s (small data) | 2026-11-19 | OK | Backup, encrypt, checksum, restore, wrong passphrase, tampered file and name guards all behaved. Cloud run still to do. |
| 2026-11-19 | local test folder, development database plus sample uploaded files | automated (local drill, not R2, not Supabase) | under 1 s | 2026-11-19 | OK | Database and uploaded files restored; a database reference to a missing file was reported as incomplete (exit 3); a hostile archive was refused before anything was created. |
| (first real drill on staging) | | | | | not yet run | |
