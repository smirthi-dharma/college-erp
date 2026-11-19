# WhatsApp absence digest: design and acceptance checks (pilot item 7)

Status: **APPROVED and BUILT** (branch `feat/whatsapp-digest`), with my recommended answers to Q1-Q8: Celery + Redis (Q1),
primary guardian only (Q2), one platform number with per-college identifiers (Q3), child name + period + subject (Q4),
opt-out by reply (Q5), minimal consent screen included (Q6), message log kept 12 months (Q7; retention job built 2026-11-11, see below),
Tamil wording is a DRAFT until someone checks it (Q8). Tests: `messaging/tests/` (check numbers are in the test names; every
check 1-25 has at least one test; check 26 is the manual run below and is NOT done).
Verified by mutation: removing the consent guard, the signature check or the double-send guard each fails tests.

**Manual check 26 (not done):** run on Meta's test number to Smirthi's own phone, then record the date and result here.

## How to run it
- Needs env vars (never in git): `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
  `FIELD_ENCRYPTION_KEYS` (comma-separated, newest first; generate with `manage.py new_encryption_key`; rotation and backup in `docs/KEY_MANAGEMENT.md`), and `REDIS_URL`
  for the background worker.
- Worker + scheduler: `celery -A college_management_system worker -B -l info`. Without Redis: `manage.py run_digest [--schema demo] [--ignore-time]`
  (also fine from cron: a day is never sent twice).
- Webhook URL to give Meta: `https://<any college or platform host>/webhooks/whatsapp/` with your verify token.
- As college admin: sidebar "WhatsApp digest" -> settings (off and dry run by default), consent, daily report, usage.
- Platform staff set the per-message rate in `/platform-admin/` (UsageRate); with no rate the cost shows 0.

## Not built / known gaps
Meta's real prices and rules not checked against
current documentation (the category defaults to utility and is corrected from Meta's webhook when it reports pricing); the webhook
scans every college for a STOP reply (fine for a few colleges, needs an index table at scale); the Redis service, worker and beat are
not yet in the Coolify setup; Tamil wording unreviewed.
Requirements come from Smirthi's brief of 2026-11-10; the decisions I had to add are marked **(my addition)**.

## What it is
Once a day, at a time each college chooses, every guardian whose child (or children) was marked absent that day gets
**one** WhatsApp message listing which child missed which periods. It uses Meta's WhatsApp Cloud API with pre-approved
template messages only, in Tamil or English. Nothing is sent to a guardian who has not said yes (DPDP Act).

Built against Meta's **test phone number** first. Real parents are messaged only after Smirthi switches a college on.

## How it works (one paragraph)
A scheduled job runs every minute. For each college it asks: is the digest on, is it past that college's send time, and
has today's run already started? If so it builds the day's list of absent students from the attendance records, groups
them by guardian, and for each guardian writes a `DigestMessage` row **before** calling WhatsApp. The unique row is what
stops a second send. The send goes through `main_app.whatsapp`, which re-checks consent. Meta later reports
sent / delivered / read / failed through a webhook, which updates the row and the college's usage count.

## Parts to build
1. **Sender upgrade** in `main_app/whatsapp.py`: the free-text `send_whatsapp(guardian, text)` stub is replaced by
   `send_template(guardian, template_key, params)`. The consent guard stays, and so does the rule "nothing else may
   reach the transport". There is no free-text path any more **(my addition)**.
2. **New tenant app `messaging`**: `DigestSettings`, `DigestRun`, `DigestMessage`, `DeliveryEvent`, `UsageRate`.
3. **Background job** (Celery + Redis, both already in the chosen stack but not yet installed: see Q1).
4. **Webhook** for delivery status and opt-outs, on the public site, routed to the right college.
5. **Admin pages**: settings, daily report, monthly usage per college.
6. **Language field** on `Guardian` (`en` / `ta`) **(my addition)**.
7. **Encrypted per-college credentials** (see "Shared piece: encrypted credentials" below).

## Decisions I made (change any of them)
- **Which absences**: only the status `absent`, for classes taken that calendar day (India time). `on_duty` is not an
  absence. Classes marked after the send time are not sent later (that would be a second message); the report counts them.
- **Who gets it**: every student with at least one absence is looked up; the message goes to that student's guardian(s)
  with consent `yes`. A guardian of two absent siblings gets one message. Default is the **primary guardian only** (Q2).
- **Nothing to report, nothing sent**: no message on days without absences, holidays, or when no class was marked.
- **Template text** (to submit in Meta's WhatsApp Manager, category *Utility*; Tamil to be checked by a native speaker):
  - `absence_digest_en`: "Dear {{1}}, on {{2}} the following were marked absent: {{3}}. For any query please contact the
    college office. - {{4}}"
  - `absence_digest_ta`: the same four variables in Tamil.
  - {{1}} guardian name, {{2}} date, {{3}} one line such as `Aarthi Kumar: P1 Programming, P3 Mathematics; Arjun Kumar: P2 Programming`,
    {{4}} college name. Meta does not allow line breaks in a variable, hence the single line.
- **Credentials and costs**: one platform WhatsApp account for the pilot, with the sender identifiers kept per college so
  moving a college to its own number later changes data, not code (Q3).

## Acceptance checks
Security and isolation (written first)
1. College B can never see or change College A's digest settings, messages, events or usage (tenant isolation test on
   every new model and page).
2. Every new URL has an explicit role decorator and the URL-walker test pins them. Settings, reports and usage pages:
   college admin only; teacher, student and head of department get 403.
3. **Consent**: a guardian with consent `unknown` or `no` is never sent to. The transport is never called, a
   `skipped_no_consent` row is written, and the report counts it. Consent withdrawn after the run began but before that
   guardian's turn is honoured (the existing re-read from the database stays).
4. **No free-text path**: a test fails if any module other than `main_app.whatsapp` can reach the transport, and
   `send_template` rejects an unknown template key or a language with no approved template.
5. The webhook accepts a request only with a valid `X-Hub-Signature-256` made from the app secret; a bad or missing
   signature gets 403 and changes nothing. The GET verification handshake works only with the right verify token.
6. The access token and app secret are stored encrypted, never appear in logs, the audit log, error pages or any
   rendered HTML (only the last four characters), and changes to them are audited without their values.
7. A webhook for a message that belongs to College A updates College A's row only (routing by message id through a
   public-schema table; a forged or unknown message id is ignored and logged).

Sending rules
8. **One message per guardian per day**: a guardian with two absent children and several absent periods gets exactly one
   message listing all of them, grouped by child.
9. **Never twice for the same day**: a database unique constraint on (guardian, date) for live sends; running the job
   twice, two workers at once, a worker killed and restarted, and a manual re-run all send once (concurrency test).
10. A guardian whose children were all present, and a day with no classes, get nothing.
11. Only `absent` counts: `on_duty` (pending or approved) and `present` are not listed. A student withdrawn from a
    subject that day is not listed for it.
12. The message goes out in the guardian's language; a guardian with no language set gets the college default. Both
    templates render with the same four variables and stay within WhatsApp's body-length limit (longer lists are cut
    with "and N more, please contact the college").
13. The job sends only after the college's send time (India time), only if the digest is switched on, and re-checks the
    switch before every message, so turning it off stops a run in progress.

Retries and failures
14. A transient failure (network error, Meta 5xx, rate limit 429) is retried up to 3 times with growing delays; every
    attempt is logged with the error.
15. A permanent failure (number not on WhatsApp, template rejected, bad token) is **not** retried, is marked `failed`
    with Meta's reason, and shows on the report. A bad or expired token stops the whole college's run at once and flags
    it "needs attention" instead of failing every guardian one by one.
16. An **unclear outcome** (timeout after the request was sent) is marked `unknown` and is **not** retried
    automatically, because the message may have gone out; the admin sees it and decides. This is what makes check 9 hold.
17. A crash in one guardian's send never stops the others.

Switches and dry run
18. Per-college `enabled` (default **off**) and `dry_run` (default **on**) **(my addition: safe defaults)**. A new college
    never messages anyone until an admin turns it on and turns dry run off.
19. Dry run: builds and logs the exact message each guardian would get (`dry_run` rows), calls no transport, counts no
    cost, and does not block a real send later the same day.
20. Changing any setting (switch, dry run, send time, language) is audited with who and when.

Delivery, report and cost
21. Webhook statuses (sent, delivered, read, failed) update the message row and are kept as append-only `DeliveryEvent`
    rows. Out-of-order or repeated events never move a message backwards (delivered is not overwritten by sent).
22. **Daily report** (college admin): for a chosen date, counts of eligible guardians, sent, delivered, read, failed,
    unknown, skipped (no consent / no phone / dry run), with the list and reasons, and a CSV download. The numbers equal
    the rows in the database (test compares them).
23. **Usage and cost per college**: each live message is counted once per month with Meta's category and our rate for
    that date (rates are rows in `UsageRate`, never hard-coded). Failed and dry-run messages cost nothing. A monthly
    report per college with a CSV, labelled "estimate until reconciled with Meta's invoice".
24. **Opt-out (my addition)**: a guardian replying STOP (or the Tamil equivalent) sets consent to `no` through the
    audited path, and the next digest skips them (Q5).

Quality
25. Works with a fake transport in the test suite; **no test touches the network**. Full suite stays under 5 minutes.
26. One manual end-to-end run on the Meta test number, to Smirthi's own phone, recorded in this file with the date.

## Things only Smirthi can do (needed for check 26 and the webhook)
1. developers.facebook.com: create an app, add the WhatsApp product, note the **test phone number id**, the **WhatsApp
   Business Account id**, the **app secret** and a temporary access token. Add your own number as an allowed test recipient
   (the test number can only message a few pre-registered numbers).
2. In WhatsApp Manager, submit the two templates above (Utility). Approval can take a day; Tamil is a separate template.
3. The webhook needs a public HTTPS address: staging (PR #13 steps) or a temporary tunnel on your Mac.
4. Never paste the token or app secret in chat: put them in `.env` / Coolify; I will read them from the environment only.
Meta's current limits, template rules and price list change, so I will check them against Meta's documentation when
building and note anything that differs from this page.

## Shared piece: encrypted credentials
The WhatsApp token and later each college's Razorpay keys (see `docs/FEES_DESIGN_NOTES.md`) need the same thing: a
per-college secrets table whose values are encrypted with a key from the environment (not the database), shown only as
the last four characters, replaceable, audited without values, and absent from logs. Build it once, in this feature, and
reuse it for fees.

## Not in this feature
Replies and two-way chat, free-text announcements, fee reminders and marks alerts (they will reuse the sender and the
job), SMS fallback, per-subject or per-class messages, a parent login, message scheduling per class, automatic re-send of
corrections made after the send time.

## Open questions (my recommendation first)
- Q1. **Background job engine.** Recommend **Celery + Redis** as in the stack decision: it adds a Redis service, a worker
  and a beat scheduler to Coolify. Alternative: a management command run every minute by cron on the VPS, which is
  simpler but has no built-in retry or visibility. Retries and logging were requirements, so Celery.
- Q2. **Which guardians.** Recommend the **primary guardian only** (one message per student household, lower cost, less
  noise). Alternative: every guardian with consent yes (father and mother each get one).
- Q3. **Whose WhatsApp number.** Recommend **one platform number for the pilot** and record per-college identifiers, so a
  college that wants its own display name and number can move later. Alternative: each college brings its own Meta
  business account from day one (more setup for every college, no shared cost to re-bill).
- Q4. **What the message shows.** Recommend child name, period number and subject name. Alternative: period numbers only
  (shorter, less personal data on a phone lock screen).
- Q5. **Opt-out by reply.** Recommend yes: Meta's policy expects opt-outs to be honoured, and DPDP requires withdrawal to
  be as easy as giving consent.
- Q6. **Consent recording.** Today consent is `unknown` for everyone and there is no screen to set it, so nothing could be
  sent to a real parent. Recommend a **minimal admin-only consent screen in this feature** (yes / no, how it was obtained,
  date, audited), because without it the digest cannot be used. Alternative: build the fuller consent feature first.
- Q7. **How long to keep the message log** (it holds child names). Recommend 12 months, configurable, and deleted with the
  student's data when a DPDP deletion is processed.
- Q8. **Tamil text.** Who will check the Tamil template wording before it goes to Meta (a faculty member)?

## Retention and resend (added 2026-11-11, branch `feat/retention-resend`)
- **Retention.** Message rows (they hold child names) older than the college's `retention_months` (default 12, allowed 1-60, set on the
  settings page) are deleted every night at 03:30 IST by `messaging.tasks.purge_old_messages` (Celery beat), or by hand with
  `manage.py purge_message_log [--schema X] [--dry-run]`. A message exactly 12 months old is kept; delivery events go with their
  message; a run row goes when it has no messages left. Guardians, consent records and the audit log are never touched. A purge
  that deleted something writes one audit entry (count and cutoff date, no names). The settings page shows when it last ran.
- **Resend.** The daily report shows "Send again..." on `unknown` (unclear) live messages. It opens a page that warns the parent
  may receive the message twice and needs a ticked box. Only the college admin; only `unknown` messages that never got a
  WhatsApp id; at most 3 times; only for messages of the last 7 days. The message goes back through the normal sender, so consent
  and the on/off switch are re-checked. Each resend is audited with who did it and counted on the row. A double click sends once.

## Consent evidence (added 2026-11-11, branch `feat/consent-evidence`)
Every consent decision is now a `main_app.ConsentRecord`: who recorded it (id, e-mail, role, copied), when, how (paper form,
verbal, parent self-service, or the system for a WhatsApp STOP), a note, and an optional scan of the signed form. Records are
append-only (a withdrawal is a new row); `Guardian.whatsapp_consent` is only the current answer, changed only through
`main_app.consent.record_consent`. Verbal consent needs a note. The college admin's screen offers paper form and verbal only;
"parent self-service" is reserved for the future parent portal so staff cannot claim it. Scans: PDF, PNG or JPEG, up to 5 MB,
magic bytes checked, stored under a random name in the college's own media folder, and downloadable only by the college admin
through a view (attachment, no-store), never from a public media URL. Guardians whose consent predates this show
"No evidence on file". Known gaps: a scan is not virus-scanned; a deleted guardian's records are deleted with them (the DPDP
erasure design must decide what proof to keep).
