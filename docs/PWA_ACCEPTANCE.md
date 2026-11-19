# Installable web app (PWA): what is built, how it is checked, what is left

Decision (2026-11-19, `CLAUDE.md`): the pilot ships as server-rendered Django plus a PWA; the Play Store comes later through a
Trusted Web Activity wrapping the same site. Built on branch `feat/pwa`.

## What is built

| Piece | Where | Notes |
|---|---|---|
| Web app manifest | `/manifest.webmanifest` (`main_app/pwa.py`) | One per college: its name, "standalone" display, start page `/`, theme colour, icons 192, 512 and a maskable 512 |
| Icons | `main_app/static/pwa/`, drawn by `scripts/make_pwa_icons.py` | Plain mortarboard on the theme colour, no name or brand; a college can get its own later |
| Service worker | `/sw.js` (`main_app/templates/pwa/sw.js`) | Scope `/`; always re-checked by the browser; cache names carry `PWA_CACHE_VERSION` (set `GIT_COMMIT` in the deploy) so a new version replaces the old caches |
| Offline page | `/offline/` | Shown when a page cannot be opened because there is no connection. Says plainly that nothing was saved |
| Install prompt | `main_app/static/pwa/register.js` | A small "Install" bar when the browser offers installation; "Not now" is remembered |
| Page head | `main_app/templates/pwa/head.html`, included by the login, signed-in and password-reset pages | manifest link, theme colour, home-screen icon, worker registration |

## The privacy rule (the reason the worker is so small)

Phones in a college are shared and the data is personal (DPDP Act). The worker therefore keeps **only** the offline page and
public static files (CSS, scripts, icons). It never keeps an HTML page of the app, a picture, a form post or any data, so
nothing about a student or a college can stay on a phone or be shown to the next person, and signing out needs no clean-up.
Opening a page always goes to the network; only when there is no connection does the offline page appear. Offline attendance
marking is **not** built (a later item, and it would need its own privacy design).

## Acceptance checks

| # | Check | How it is proved |
|---|---|---|
| 1 | The manifest is public, valid JSON of the right type, has name, short name (12 characters or fewer), start page, scope, standalone display, theme and background colours, and 192, 512 and maskable icons | `test_pwa.ManifestTests` |
| 2 | Every icon the manifest names exists and has the size it claims; the Apple icon is 180 px | `test_pwa.ManifestTests` |
| 3 | Each college gets its own name and never another college's | `test_pwa.ManifestTests` (two colleges) |
| 4 | The worker is served with `Service-Worker-Allowed: /` and `Cache-Control: no-cache`, from the root | `test_pwa.WorkerFileTests` |
| 5 | The worker file runs for real (in Node): install saves the offline page and icons; activate deletes old versions and leaves other caches alone | `test_pwa.WorkerBehaviourTests` |
| 6 | Opening a page online uses the network and keeps nothing; offline shows the offline page and keeps nothing | `WorkerBehaviourTests`, mutation-checked |
| 7 | Posts, other hosts, pictures, data and look-alike paths are never touched or cached; the only thing ever written to a cache is a static file | `WorkerBehaviourTests`, mutation-checked |
| 8 | The offline page is public, small, sets no cookie, holds no personal data, and says nothing was saved | `test_pwa.OfflinePageTests` |
| 9 | Every kind of page (login, admin, teacher, student, password reset) links the manifest, theme colour, icon and register script | `test_pwa.PagesAreInstallableTests` |
| 10 | The Content-Security-Policy lets the manifest, worker and script load | `PagesAreInstallableTests` |
| 11 | The three new URLs are the only additions to the public-page list | `test_access_control.PolicyTests` |
| 12 | In real Chrome on the development server: the worker registers, activates and controls the page; the offline page and icons are cached; static files are cached on the second load; no HTML page is cached; with the server stopped, opening a page shows the offline page; "Try again" recovers after the server is back | done 2026-11-19 by hand, see below |
| 13 | **Installs from Chrome on a cheap Android phone, opens full screen with the right icon and name, shows the offline page in airplane mode, and a class of attendance is taken in under 30 seconds** | **NOT DONE: needs a real phone, see the checklist below** |

## Checked by hand in real Chrome (desktop, development server, 2026-11-19)

Registered and activated at scope `/`; `erp-shell-dev` held `/offline/` and the two icons; after a second load `erp-static-dev`
held CSS, jQuery, fonts and `register.js` and **no** HTML page; with the server stopped, opening `/students/` showed the
offline page titled with the college name; after the server returned, "Try again" loaded the login page. I then removed the
worker and caches from that browser. The built-in preview pane of the desktop app blocks service-worker fetches, so it could
not be used for this. I have not tested on an Android phone, and have not run Lighthouse; Chrome's own install prompt
(`beforeinstallprompt`) was not observed on desktop.

## Real-phone checklist (you do this on the cheap Android; 20 minutes)

Needs HTTPS (staging, `docs/STAGING_SETUP.md`); a service worker does not run on plain HTTP except on `localhost`.

1. Open the college address in **Chrome** on the phone. After a few seconds an "Install" bar appears at the bottom (or use the
   menu, "Install app"). Install it. Check: the icon (a mortarboard) and the college's name are on the home screen.
2. Open it from the icon: it opens **full screen**, with no address bar, on the login page, and the status bar is the theme colour.
3. Log in as a teacher, open **Attendance**, take one class with a stopwatch: start when the class page is open, stop when the
   confirmation shows. Write the time, the phone model and Android version in `docs/ATTENDANCE_ACCEPTANCE.md`.
4. Switch on **airplane mode**, then tap another menu item: the "You are offline" page appears (not Chrome's dinosaur). Switch
   airplane mode off and tap "Try again": the page loads.
5. In airplane mode, on the attendance page, tap Submit: nothing is saved and the app says so (the offline page); after
   reconnecting, repeat the class and confirm it saved once.
6. Sign out, then open the app again: the login page, and no earlier student's page is visible anywhere (use the back button).
7. Note anything slow, ugly or confusing in the PR.

## Not built (said plainly)

- Offline attendance marking and any other offline use of data.
- Push notifications (WhatsApp covers alerts).
- A per-college icon and colour; the generic icon is used.
- Lighthouse score and the Play Store (Trusted Web Activity) wrapper; both come after the phone test.
- The CSP still allows inline scripts (`docs/SECURITY_AUDIT.md`), which is unrelated to the worker.
