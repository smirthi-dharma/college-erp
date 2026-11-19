# Restore test log

One line per drill (`docs/BACKUPS.md`, section 3). The monthly drill is a non-negotiable: a backup that has never been restored
is not a backup.

| Date | Backup file | Done by | Time to restore | Newest audit entry in it | Result | Notes |
|---|---|---|---|---|---|---|
| 2026-11-19 | local test folder, development database | automated (local drill, not R2, not Supabase) | under 1 s (small data) | 2026-11-19 | OK | Backup, encrypt, checksum, restore, wrong passphrase, tampered file and name guards all behaved. Cloud run still to do. |
| (first real drill on staging) | | | | | not yet run | |
