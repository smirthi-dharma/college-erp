# Encryption key management

What this protects: the secrets we keep for each college in the database. Today that is the college's own WhatsApp access token
(`messaging.CollegeSecret`); later each college's Razorpay keys (see `docs/FEES_DESIGN_NOTES.md`). They are encrypted with Fernet
(AES-128-CBC with an HMAC) using keys held **only in the environment**. The database never holds a key, so a leaked database dump
or backup does not reveal the secrets, and a leaked key without the database reveals nothing.

## The settings
| Variable | Meaning |
|---|---|
| `FIELD_ENCRYPTION_KEYS` | Comma-separated keys, **newest first**. New secrets are encrypted with the first key; any key in the list can read. Use this. |
| `FIELD_ENCRYPTION_KEY` | The original single-key setting. Used only when `FIELD_ENCRYPTION_KEYS` is empty. |

Make a key: `python manage.py new_encryption_key` (prints one line, nothing is stored).

## Backing the key up (do this BEFORE the first real secret is saved)
Losing every key that encrypted a value makes that value **unrecoverable**: each college would have to enter its token again. So:
1. Put every key in the team password manager, in an entry named for the date it was created and labelled "college-erp encryption key".
2. Keep a second copy somewhere that fails independently of the first: a sealed printed copy in a safe, or a second person's
   password manager. Two people should be able to find it.
3. **Never** in git, in chat, in a screenshot, in `.env.example`, in a ticket, or next to the database backup. A backup of the
   database and a copy of the key must not live in the same place: together they are the secrets.
4. Coolify holds the key as an encrypted environment variable on the app. That is the working copy, **not** a backup.
5. After any key change, repeat 1 and 2 before you remove an old key anywhere.
6. The monthly restore test includes this step: restore the database to a scratch server, set the keys from the password
   manager (not from Coolify), and run `python manage.py rotate_encryption_keys --check`. It must say 0 unreadable.

## Rotating (every 12 months, or at once if a key may have leaked)
1. `python manage.py new_encryption_key` and store the new key (steps 1-2 above).
2. Set `FIELD_ENCRYPTION_KEYS=<new>,<old>` in Coolify (new first, old kept) and redeploy. Nothing breaks: old values still read.
3. `python manage.py rotate_encryption_keys` (Coolify terminal). It re-encrypts every college's secrets with the new key and
   prints `N secret(s) re-encrypted, M already on the newest key, 0 unreadable`. Each change is written to the audit log
   (the ciphertext changed; never its value). It is safe to run again.
4. `python manage.py rotate_encryption_keys --check` must print `0 secret(s) would need re-encrypting` and exit 0.
5. Keep the old key in the list for at least one nightly backup cycle: a database backup taken before step 3 still holds values
   encrypted with the old key, and you need the old key to read it if you ever restore it. Retire the old key from Coolify
   only when you no longer need to restore any backup made before the rotation, and keep it in the password manager (marked
   "retired, kept to read backups from <date range>") for as long as those backups exist.
6. If any secret is `unreadable`, STOP: the command exits with an error and changes nothing for that row. Do not remove any old
   key. Find the missing key (password manager, second copy) and add it to the list.

## If a key leaks
Add a new key at the front, run the rotation, then **also** ask each college to replace the secret itself (a new WhatsApp token,
new Razorpay keys), because the leaked key plus an old database copy would still read the old values. Rotation protects the
values from now on; it cannot un-leak what a copy already holds.

## If every key is lost
The stored secrets cannot be recovered. Stored tokens must be entered again by each college admin (settings -> WhatsApp digest).
Nothing else is encrypted with these keys, so no student or fee data is lost.

## What the tests prove
`messaging/tests/test_key_rotation.py`: new values use the first key; old values still read; rotation re-encrypts every college
and keeps the plaintext; a second run changes nothing; `--check` changes nothing; an unreadable secret stops the run and changes
nothing; rotation is audited without values; a malformed, duplicate or missing key is refused without printing a key.
