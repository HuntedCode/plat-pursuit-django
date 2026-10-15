# Security Hardening

Security measures, configurations, and audit findings for the PlatPursuit application.

## Security Headers

### Configured in `plat_pursuit/settings.py`

| Header | Setting | Value |
|--------|---------|-------|
| HSTS | `SECURE_HSTS_SECONDS` | 31536000 (1 year), includes subdomains, preload |
| SSL Redirect | `SECURE_SSL_REDIRECT` | True (production) |
| X-Content-Type-Options | `SECURE_CONTENT_TYPE_NOSNIFF` | True (nosniff) |
| X-Frame-Options | `X_FRAME_OPTIONS` | DENY |
| CSRF Cookie | `CSRF_COOKIE_SECURE` | True |
| CSRF Cookie HttpOnly | `CSRF_COOKIE_HTTPONLY` | True (Safari ITP drops non-HttpOnly cookies more aggressively) |
| CSRF Cookie SameSite | `CSRF_COOKIE_SAMESITE` | `'Lax'` (Safari ITP compatibility) |
| Session Cookie | `SESSION_COOKIE_SECURE` | True |
| Session Cookie SameSite | `SESSION_COOKIE_SAMESITE` | `'Lax'` (Safari ITP compatibility) |

**Safari ITP context**: The three SameSite/HttpOnly settings were added after Safari users started failing CSRF checks immediately after login. Safari's Intelligent Tracking Prevention drops non-HttpOnly cookies on cross-site contexts and treats `SameSite=None` cookies more aggressively than Chrome/Firefox. Setting all auth-relevant cookies to `HttpOnly + SameSite=Lax` resolved the symptom and is the documented Safari-friendly configuration. Do not relax these without re-testing on Safari.

### Content Security Policy (django-csp)

CSP is configured via `CONTENT_SECURITY_POLICY` in settings.py using the `django-csp` package (`csp.middleware.CSPMiddleware`).

| Directive | Allowed Sources |
|-----------|----------------|
| `default-src` | `'self'` |
| `script-src` | `'self'`, `'unsafe-inline'`, `cdn.jsdelivr.net`, `static.cloudflareinsights.com` |
| `style-src` | `'self'`, `'unsafe-inline'` |
| `font-src` | `'self'` (fonts self-hosted since SEO Lane 3, 2026-08) |
| `img-src` | `'self'`, `data:`, PSN domains (http + https), `*.s3.amazonaws.com`, `images.igdb.com` |
| `frame-src` | `'self'`, YouTube embed domains |
| `connect-src` | `'self'`, `cdn.jsdelivr.net` (source maps), `cloudflareinsights.com` |
| `frame-ancestors` | `'none'` |

**Removing AdSense tightened this policy (2026-08).** Roughly 20 Google ad origins came out across four
directives (googlesyndication, doubleclick, adtrafficquality, Funding Choices, googletagservices,
adservice, `www.gstatic`, `csi.gstatic`), along with **`'wasm-unsafe-eval'`** — AdSense creatives were the
app's only WebAssembly consumer — and the `http://fonts.gstatic.com` twin, which existed because AdSense
creatives loaded Google Sans over plain http.

Two entries were deliberately **kept** at the time because they sat beside ad origins without being
ones: `https://fonts.gstatic.com` on `font-src` (then our webfont stack) and `images.igdb.com` on
`img-src` (our cover art; only its `connect-src` copy was AdSense's scanner). The fonts entry has
since been removed on purpose: SEO Lane 3 (2026-08) self-hosted the fonts into `static/fonts/`, so
`font-src` is `'self'` alone and `test_csp_fonts_are_self_only` pins that. `images.igdb.com` remains
pinned by `tests/engine/test_ads_removed.py`.

**Note:** `'unsafe-inline'` is required for scripts (template `<script>` blocks) and styles (Tailwind). A future improvement would be nonce-based script loading to remove `'unsafe-inline'` from `script-src`.

## Rate Limiting

Two systems are in play:

- **`django-ratelimit`** handles app-level endpoints, keyed by `user` (most) or `ip` (unauthenticated), with `block=True` (returns 429 on excess).
- **`ACCOUNT_RATE_LIMITS`** (allauth) handles the auth-flow endpoints (signup, login, password reset, email confirm). Configured in `plat_pursuit/settings.py`.
- **DRF's throttle** (`core.throttling.VisitorUserRateThrottle`, `DEFAULT_THROTTLE_RATES['user']`, 60/min) covers every DRF view: signed-in callers by user, anonymous ones by visitor IP.

**The Discord bot** calls PlatPursuit for every Discord member through one account, so a per-user limit made them all share one bucket: on a busy minute every member's commands failed together. django-ratelimit skips it (`api.views.bot_exempt_rate`), and the DRF throttle gives it its own bucket at `DEFAULT_THROTTLE_RATES['bot']` (600/min, ten times the user rate). That ceiling stays on purpose: it is the bot's only hard limit, and the bot token is accepted across most of the API, including generate-code and refresh, which start PSN syncs. A leaked BOT_API_KEY or a retry loop in PlatBot must still be capped. `core.bot_auth.is_discord_bot` is the single answer to "is this the bot?" for the permission, the exemption, the ceiling and anything new.

### Client IP (who "per IP" means)

`REMOTE_ADDR` is always Render's internal proxy: the same address for every visitor. `core/client_ip.client_ip()` works out the visitor instead, and every IP-keyed path uses it:
- allauth (`users/adapters.py` overrides `get_client_ip`)
- django-ratelimit (`RATELIMIT_IP_META_KEY`)
- DRF throttling (`core/throttling.VisitorUserRateThrottle`; DRF's own anonymous key is the raw `X-Forwarded-For` header, so a forged value per request was a fresh bucket; IPv6 visitors are bucketed by their /64, as allauth and django-ratelimit already do, since a home connection gets a whole /64 to rotate through)
- ModerationLog and ReviewModerationLog
- the Cloudflare-bypass log line

| Connecting hop (rightmost public `X-Forwarded-For` entry) | Visitor |
|----------|---------|
| None (dev, tests) | `REMOTE_ADDR` |
| A Cloudflare address | `CF-Connecting-IP` |
| Anything else | that hop |

Only the entries the proxies append can be trusted; everything to their left is written by the client. So the helper reads from the right, skips internal hops (anything not globally routable, including shared address space 100.64.0.0/10), and believes `CF-Connecting-IP` only when the connecting hop is Cloudflare. The ranges live in `CLOUDFLARE_NETWORKS` (from https://www.cloudflare.com/ips/).

**Render fronts every service with its own Cloudflare.** This is not in Render's docs; it comes from a third-party header test against an onrender.com service, which saw a Cloudflare hop, a Cloudflare-set `CF-Connecting-IP`, and Cloudflare answering a forged `CF-Connecting-IP` with a 403 (error 1000) before it reached the app. So both production (behind our Cloudflare too) and beta take the Cloudflare row, and the last row only matters if Render ever changes. Confirm on both at `/staff/request-ip/` (staff only, never cached): `client_ip` should be your own address and `x_forwarded_for` should end in a Cloudflare hop.

### Auth Rate Limit Coverage (allauth)

| Endpoint | Rate | Notes |
|----------|------|-------|
| `signup` | `5/m/ip` | Tighter than allauth default (20/m/ip). Blunts botnet mass-signup that weaponizes the verification-email send as third-party email bombing. |
| `login_failed` | `10/m/ip,5/300s/key` | Per-IP global + per-email throttle. |
| `confirm_email` | `5/m` | |
| `reset_password` | `5/m` | |

### App Rate Limit Coverage (django-ratelimit)

All user-facing POST/DELETE endpoints should have rate limits. Current coverage:

| Category | Endpoints | Rate |
|----------|-----------|------|
| Easter eggs | roll, claim | 20/m, 10/m |
| Comments (legacy) | vote, report, edit/delete | 30/m, 5/h, 20/m |
| Reviews | create, vote, report, replies | 10/m, 30/m, 5/h, 10/m |
| Game flags | submit | 5/h |
| Roadmap editor | step CRUD, image upload | 30/m, 10/m |
| Challenges | create (all types) | 30/h |
| Device tokens | register, delete | 10/m |
| Titles | equip | 15/m |
| Fundraiser | donate | 5/m |
| Dashboard | config, reorder, preview | 15/m |
| Subscription admin | actions | 10/m |
| Notifications | admin send | 10/m |

## Signup Honeypot

`users/forms.CustomUserCreationForm` defines an extra `website` field that is positioned off-screen in `templates/account/signup.html` (`left: -9999px`, `aria-hidden`, `tabindex=-1`). Real users never see or tab to it; scripted form-fillers populate every input they encounter and trip the validator, which raises a generic error so the bot cannot tell which field rejected the submission. Pairs with the `signup` rate limit to catch attackers using residential proxies to stay under the per-IP throttle.

## Log Noise Suppression

`plat_pursuit/logging_filters.SuppressBotMagnet405` is attached to the console handler in `LOGGING`. It drops the `Method Not Allowed (POST): /accounts/confirm-email/` warnings (and equivalent on `/xmlrpc.php`, `/wp-login.php`, `/wp-admin*`) that spray-and-pray scanners fire dozens of times per minute. The filter is narrowly pattern-matched, so legitimate 405s on real endpoints still surface.

## CORS Policy

CORS is configured via `django-cors-headers`. Production origins come from the `CORS_ALLOWED_ORIGINS` environment variable. Development origins (`localhost:8081`, `localhost:19006`) are only added when `DEBUG=True`.

## Easter Egg Security

Easter egg probability rolls are performed server-side. The client cannot determine outcomes. See [Easter Eggs](../features/easter-eggs.md) for the full architecture.

## Admin Log Privacy

Admin action logs use `user_id` instead of email addresses to avoid PII in log storage. This applies to notification sending, subscription admin actions, and fundraiser claim management.

## Dependency Advisories

### Where each dependency tree runs

The two trees carry very different risk, so weigh advisories by where the code actually executes.

| Tree | Installed by | Reaches users? |
|------|--------------|----------------|
| npm (`package.json`) | Dev machines and CI (`npm ci`, then `npm run build` / `npm run lint`) | **No.** Tailwind compiles `static/css/output.css`, which is committed; the Dockerfile never runs npm. The one npm package whose code is served, SortableJS, ships as a hand-vendored copy (`static/js/vendor/Sortable.min.js`, 1.15.7), not from `node_modules`. |
| Python `requirements.txt` | The production Docker image | **Yes.** This is the runtime. |
| Python `requirements-dev.txt` | Dev and CI only | No. |

An npm advisory is therefore a build-machine concern (crafted input to a build tool), while an advisory in `requirements.txt` is a production concern.

### Running a triage

| Step | Command |
|------|---------|
| npm audit | `npm audit` and `npm audit --omit=dev` (identical today, because the Tailwind toolchain is listed under `dependencies`) |
| npm fix | `npm audit fix` **without** `--force`, so `package.json` ranges are respected and only the lockfile moves |
| Verify npm | `npm run build`, then confirm `git diff --quiet static/css/output.css` (a raw file hash differs on Windows even when the content is identical, because the build writes LF line endings); `npm run lint`; if `@parcel/watcher` moved, smoke-test `npm run watch` with a real content change |
| Python audit | `pip install -r requirements-dev.txt` (it pins `pip-audit`), then `pip-audit -r requirements.txt --no-deps --disable-pip`. Every transitive dependency is pinned, so `--no-deps` audits exactly what ships. CI runs the same command as a gate ("Audit Python dependencies" in `.github/workflows/tests.yml`); to accept an advisory with no usable fix, add `--ignore-vuln <ID>` there and a row here saying why |
| Python pins | Every installed package must be pinned, or the audit cannot see it. CI checks this ("Check the Python pins": install the pins with `--no-deps`, then `pip check`), so a bump that needs a new or changed transitive pin fails there. To find the right pin, install `requirements.txt` into a clean Linux Python 3.12 environment (`docker run --rm -it -v "$PWD:/src" python:3.12-slim bash`, as the image runs; Windows evaluates platform markers differently) and compare `pip freeze` with the pins |

### Dependabot

| What | Setting |
|------|---------|
| Security updates | On (repository settings). A PR opens as soon as an advisory affects a pinned version |
| Version updates | `.github/dependabot.yml`: on the 1st of each month, one grouped PR each for pip, npm and GitHub Actions with every minor and patch update (date-versioned pip packages like `certifi` and `tzdata` go in a second pip group, whatever the version jump). Each major update opens its own PR (except the pairs below), up to 10 open at once for pip. The first round opens as soon as the config reaches `main`, not on the 1st |
| Monthly review | On the 2nd, a scheduled Claude routine opens a "Dependency review: <Month YYYY>" issue in this repo covering the open Dependabot PRs and their CI results across PlatPursuit, PlatBot, Portfolio and Glimmlings. It cannot read Dependabot alerts, so check each repo's Security tab alongside it |
| Held back on purpose | Django majors (see below), pycountry majors (psnawp pins it below 25). Ignored with `update-types`, so their security PRs still come |
| Upgraded as pairs | `dj-stripe` with `stripe` (the `payments` group: test with a test-mode checkout) and `psnawp` with `pyrate-limiter` (the `psn-client` group: psnawp 3.0.1+ needs pyrate-limiter 4, whose API `trophies/token_keeper.py` uses). A group only combines updates that land in the same run: when both halves have one, they arrive as one PR; a lone update still arrives by itself and can pass CI, because CI does not exercise payments or a PSN sync end to end. **Never merge one half alone**: upgrade each pair on its own branch. The monthly review flags a lone half as "Hold" |

Reviewing a Dependabot PR: CI green means the tests, the audit and the pin check passed. For a Python bump, also skim the changelog of anything on the request path (Django, DRF, allauth, Pillow, nh3). A major update gets the same care as a hand-made upgrade: its own branch if it needs code changes. An npm bump only touches the build, but **CI does not rebuild the CSS**, so green does not cover it: check out the branch, run `npm run build`, and if `static/css/output.css` changed, commit it to the PR before merging.

**Django: LTS to LTS.** The site stays on Django 5.2 LTS (security fixes until April 2028) and skips 6.0 and 6.1. Plan the hop to **6.2 LTS** once it ships (about April 2027) and the packages here support it, leaving roughly a year before 5.2's support ends. Django 6.0 removed `CheckConstraint(check=...)` (renamed `condition=`, available since 5.1); the 6.1 run failed on it before any test ran. The only use was migration `trophies/0214_roadmap_notes.py`, switched to `condition=` on 2026-10-15, so that blocker is gone.

### 2026-10 triage

**npm: 10 advisories (1 critical, 8 high, 1 moderate), all build-only, all fixed by `npm audit fix` (lockfile only, no direct dependency changed version).** Rebuilt CSS was byte-identical.

| Package | Severity | Pulled in by | Was, now |
|---------|----------|--------------|----------|
| tar | critical | `@tailwindcss/oxide` | 7.5.2, 7.5.22 |
| postcss | high | direct (unused by the Tailwind v4 CLI) | 8.5.6, 8.5.30 |
| braces, micromatch | high | `@parcel/watcher` (watch mode) | removed (watcher 2.6.0 dropped them); back on 2026-10-15, see below |
| picomatch | high | `@parcel/watcher` | 2.3.1, 4.0.7 (the range watcher 2.6.0 asks for) |
| browserslist | high | `autoprefixer` | 4.26.3, 4.29.3 |
| nanoid | high | `postcss` | 3.3.11, 3.3.20 |
| source-map-js | high | `postcss`, `@tailwindcss/node` | 1.2.1, 1.2.2 |
| baseline-browser-mapping | moderate | `browserslist` | 2.8.16, 2.11.28 |

**Accepted npm advisory (2026-10-15):** [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm), `braces` 3.0.3 (high, stack exhaustion on deeply nested patterns), through `@tailwindcss/cli` 4.3.3 > `@parcel/watcher` 2.5.1 > `micromatch`. Tailwind 4.3.1 and later pin the watcher to exactly 2.5.1, so it cannot move to 2.6.0, and no patched `braces` exists anyway. It runs only in `npm run watch`, on this repo's own file paths, never on input from users, and nothing from npm is served. Glimmlings accepts the same advisory. Re-check when Tailwind allows `@parcel/watcher` 2.6 or a patched `braces` ships.

The `eslint@9.39.5` end-of-support warning that `npm ci` used to print went with the ESLint 10 upgrade (#160).

**Python: 96 unique advisories across 15 packages in `requirements.txt`, all cleared by 2026-10-14.** Django and Pillow, the two High rows, went first (see [Image Uploads](#image-uploads) for the Pillow follow-through); a follow-up PR took every other row. `pip-audit` now reports no known vulnerabilities, and CI fails on any new one.

| Package | Pinned | Clears all | Why it matters here | Priority |
|---------|--------|------------|---------------------|----------|
| Django | 5.2.7 | 5.2.17 (same 5.2 LTS line) | 30 advisories: SQL injection in `FilteredRelation` / `order_by` column aliases and `_connector` dict expansion, ASGI and multipart DoS, cache-middleware response leaks, `get_signed_cookie` salt collision | High (fixed: 5.2.17) |
| Pillow | 12.0.0 | 12.3.0 | 19 advisories, several heap out-of-bounds writes / memory corruption (PSD, EPS, JPEG2000, GD, PCF/BDF fonts, coordinate APIs). `trophies/image_utils.py` called `Image.open()` on user uploads with no `formats=` allowlist, so those parsers were reachable | High (fixed: 12.3.0, and uploads now reach only the allowed decoders) |
| djangorestframework | 3.16.1 | 3.17.2 | `request.data` parsing can bypass Django's upload memory limits; AdminRenderer data leak on 400s | Medium (fixed: 3.17.2) |
| urllib3 | 2.5.0 | 2.8.0 | Decompression bombs in streaming, cross-origin redirect header leak, proxy TLS settings ignored. Affects outbound calls (PSN, IGDB, Discord, S3) | Medium (fixed: 2.8.0) |
| cryptography | 46.0.3 | 50.0.0 | 46.0.7 (patch) clears three; the bundled-OpenSSL advisory and the rest need 48 to 50 (major) | Medium (fixed: 50.0.2; only `sendgrid` requires it, `>=45.0.6`, and nothing calls it) |
| bleach | 6.2.0 | 6.4.0 | Disallowed URI schemes survive in `href` when `a` + `href` are allowed, which `checklist_service.py` does. The advisory rates it as not directly exploitable in browsers | Medium (fixed: 6.4.0, then replaced by `nh3` 0.3.7 on 2026-10-14, because bleach's changelog says it is no longer maintained and future advisories will get no fix; see [HTML Sanitizing](#html-sanitizing)) |
| tornado | 6.5.2 | 6.5.9 | 14 advisories, but nothing imports tornado and no installed package requires it. Remove the pin rather than upgrade it | Low (removed) |
| black | 25.9.0 | 26.3.1 | A formatter pinned in the production requirements; belongs in `requirements-dev.txt` | Low (moved to dev as 26.10.1; its helper packages left the production pins with it and are resolved by pip in dev, since 26.x needs newer ones) |
| sqlparse | 0.5.3 | 0.6.0 | DoS on crafted SQL; Django uses it for SQL formatting, not on request input | Low (fixed: 0.6.0) |
| django-allauth | 65.13.1 | 65.14.1 | Open redirect only with SAML IdP-initiated SSO, which is not configured | Low (fixed: 65.14.1) |
| Werkzeug | 3.1.4 | 3.1.9 | `safe_join` Windows device names; only pulled in by `sendgrid`, never serves files | Low (fixed: 3.1.9) |
| Markdown | 3.7 | 3.8.1 | Crash on malformed HTML-like input; renders staff-authored broadcast emails only | Low (fixed: 3.8.1; with the app's `extra`, `nl2br`, `sane_lists` extensions it renders sample broadcasts byte-identically to 3.7) |
| requests, idna, python-dotenv | 2.32.5, 3.11, 1.2.1 | 2.33.0, 3.15, 1.2.2 | Temp-file name in a zip helper, idna DoS on long input, `.env` symlink follow (local only) | Low (fixed) |

## HTML Sanitizing

User-written HTML is cleaned by [nh3](https://github.com/messense/nh3) (Python bindings for the Rust `ammonia` sanitizer), which replaced bleach in 2026-10 after bleach's maintainers ended the project.

| Sanitizer | Where | Allows | Used for |
|-----------|-------|--------|----------|
| `CommentService.sanitize_text` | `trophies/services/comment_service.py` | No tags at all; the result is unescaped to plain text | Comments, list names (run to a fixpoint by `game_list_service`) |
| `ChecklistService.process_markdown` | `trophies/services/checklist_service.py` | `ALLOWED_HTML_TAGS` and `ALLOWED_HTML_ATTRS`, links and images on `ALLOWED_URL_SCHEMES` (http, https) or relative (including protocol-relative `//host`, as under bleach). No author `rel` or `target`: the link pass adds `target="_blank" rel="noopener noreferrer"` to every external link. Only the classes in `ALLOWED_CLASSES` (`ps-icon` on controller-icon images, `text-left`/`text-right`/`text-center` on table cells); the styling passes after sanitizing add the site's own | Roadmaps, reviews, guides (`render_markdown`, `render_roadmap_markdown`) |

Both strip disallowed tags and keep their text, with these exceptions:

- `<script>` and `<style>` are removed with their content (bleach kept the text), and so are whole `<svg>`, `<math>` and `<template>` subtrees.
- A `<` that no later `>` closes (`hold <R2 then press X`, `a<b`) would make nh3 drop the rest of the input as an unfinished tag. `sanitize_text` escapes such a `<` first, so comments keep it as bleach did. List names still refuse any leftover `<` or `>` (`game_list_service._clean_text`).

Moving from bleach was checked end to end: the real pipelines on 29 inputs (links, images, tables, spoilers, callouts, controller icons, XSS vectors) produced the same HTML apart from the script change and two entities nh3 writes as plain characters (`&quot;` as `"`), and 27 comment inputs run through both libraries matched apart from the script change. `tests/engine/test_sanitizers.py` pins the rules by behavior; loosening the allowlist (`style` on every tag, `h1`, author `rel`/`target`, nh3's own `rel`, the unclosed-`<` escape) fails it.

Fixed in the same change: an author's `rel`/`target` used to survive and, coming before the ones the link pass appends, win. `rel="opener"` let the opened page redirect the reader's tab (reverse tabnabbing).

**Author classes are removed (2026-10).** `class` used to be allowed on every tag, so an author could apply any of the site's Tailwind classes: restyle a link as a button, or add a `fixed inset-0 z-50` element that covers the page with fake content (a phishing overlay). Now nh3's `allowed_classes` keeps only the classes the pipeline itself puts there before sanitizing, and an image left with an empty `class=""` has it removed so the image pass still styles it.

## Image Uploads

User uploads (roadmap images in `api/roadmap_views.py`, notification banners in `notifications/views.py`) go through `trophies/image_utils.py`: `validate_image` first, then `process_roadmap_image` or `optimize_image`. All three open the file through `_open_upload`, which passes `formats=ALLOWED_IMAGE_FORMATS` (JPEG, PNG, WEBP, GIF) to `Image.open()`.

That allowlist is enforced **before** decoding, not after. Without `formats=`, Pillow sniffs the bytes and runs whichever of its many parsers matches, so a crafted PSD or EPS reached that parser (the source of most Pillow memory-corruption advisories) before the old format check could reject it. Any other format now fails with `UnidentifiedImageError`, which `validate_image` turns into "must be a valid JPEG, PNG, WEBP, or GIF image." (Pillow raises the same error for a corrupt file in an allowed format, hence "valid".) One variant gets through the decoder: the JPEG decoder returns multi-picture phone photos as `MPO`, and the format check after opening rejects those by name, as it did before.

Share-card rendering (`core/services/playwright_renderer.py`) inlines `/media/` files, which include admin uploads that never passed `validate_image`, so it opens them with the same allowlist; anything else is embedded untouched rather than parsed.

`tests/engine/test_image_utils.py` pins all of this with a tripwire on the TIFF parser: the tests fail if a disallowed parser runs at all, even when the upload is rejected afterwards.

| Limit | Value |
|-------|-------|
| Formats | `ALLOWED_IMAGE_FORMATS` (JPEG, PNG, WEBP, GIF) |
| Dimensions | `MAX_IMAGE_DIMENSION` (3840px), roadmap output capped at `ROADMAP_MAX_DIMENSION` (2400px) |
| File size | per call: 10 MB roadmap images, 5 MB banners |

### What gets stored

Both pipelines re-encode every upload, and only the re-encoded file is ever stored:

| Rule | How | Why |
|------|-----|-----|
| No metadata | Re-encoding writes no EXIF, so a phone photo's GPS location is dropped (the roadmap path applies EXIF orientation first). `_flatten_to_rgb` first converts every colour mode to RGB: transparency onto `FLATTEN_BACKGROUND` (white), 16-bit greyscale scaled to 8 bits rather than clipped | Greyscale and CMYK photos used to crash the roadmap watermark step, and the fallback then stored the raw file, GPS included |
| Named for its format | `_as_upload` keeps the uploaded name's stem (leading dots trimmed, capped at `MAX_STORED_STEM_LENGTH`) and sets the extension from the saved format (`SAVED_EXTENSIONS`) | Storage serves by extension: a PNG named `x.html` used to be stored, and served, as HTML |
| Refused, never stored raw | Any processing failure raises `ValidationError(PROCESSING_FAILED_MESSAGE)`; the roadmap endpoint returns it as a 400, the banner form as an error message | A raw upload keeps both its metadata and its own extension |

`tests/engine/test_image_utils.py` and `tests/engine/test_roadmap_image_upload.py` pin all three. The metadata tests use JPEG and PNG photos that really carry a GPS block (a guard test checks the fixture), so "GPS was stripped" cannot pass vacuously.

Greyscale PNGs are stored as RGB (about three times the bytes, identical to look at). Accepted: one RGB path keeps both pipelines simple. The banner pipeline does not apply EXIF orientation, so a sideways phone photo stays sideways there (banners are staff-made and the notification center is parked).

## Gotchas and Pitfalls

- **CSP and new CDN resources.** If you add a new external script, stylesheet, font, or image source, you must update the CSP directives in settings.py or the resource will be blocked. A blocked resource fails *silently* apart from a console entry: the Cloudflare analytics beacon was CSP-blocked once and the dashboard read zero traffic while the site was being hammered.
- **Never read `REMOTE_ADDR` or `X-Forwarded-For` for a client IP.** Use `core.client_ip.client_ip(request)` (and `core.throttling.VisitorUserRateThrottle` for DRF throttles). allauth 65.14.2 stopped trusting `X-Forwarded-For` by default, and the October 2026 upgrade to 65.19 quietly turned its per-IP limits into one bucket for the whole site (ten failed logins from anyone could throttle login for everyone) until allauth was pointed at the helper. A new IP-keyed feature that reads the headers itself gets either the proxy's address or a forgeable one.
- **Rate limit key.** All `django-ratelimit` decorators use `key='user'` which requires authentication. For unauthenticated endpoints, use `key='ip'` instead. Allauth's `ACCOUNT_RATE_LIMITS` uses its own format (`'<count>/<period>[/<scope>]'` where scope is `ip` or `key`).
- **CORS localhost leak.** Before this hardening, localhost origins were unconditionally added to production CORS. Always gate development-only origins behind `if DEBUG`.
- **`throttle_classes = []` disables DRF throttling.** If you see this on a view, it's intentionally removing DRF's built-in throttling. Make sure `django-ratelimit` is used as a replacement, not that throttling is simply absent.
- **Mobile signup is protected by a different rate limit and skips the honeypot.** `MobileSignupView` in `api/mobile_auth_views.py` calls allauth's base `SignupForm` directly rather than going through `SignupView`, so neither `ACCOUNT_RATE_LIMITS['signup']` nor the honeypot in `CustomUserCreationForm` apply. The view IS protected by its own `@ratelimit(key='ip', rate='3/m', method='POST', block=True)` decorator (django-ratelimit, returns 429 on excess). Web signup uses the allauth path (`5/m/ip` + honeypot); mobile uses django-ratelimit (`3/m/ip`, no honeypot). Both are throttled, just via different mechanisms.
- **Never store a user's image bytes as uploaded, and never let them pick the extension.** A new upload path should go through `process_roadmap_image` or `optimize_image` (or reuse `_flatten_to_rgb` and `_as_upload`), and treat their `ValidationError` as "refuse the upload". Falling back to the raw file "so nothing is blocked" is exactly what leaked GPS data.
- **Never `Image.open()` a user upload directly.** Use `_open_upload` (or the helpers that call it) in `trophies/image_utils.py`, or pass `formats=ALLOWED_IMAGE_FORMATS` as the card renderer does. A bare `Image.open()` runs every Pillow parser again, and a format check afterwards is too late: the parser has already run. Django's `ImageField` form validation also opens files with every decoder, but it is only reached from the staff-only admin.
- **An unpinned package is invisible to the Python audit.** `pip-audit --no-deps` checks only what `requirements.txt` names, but pip still installs whatever a pinned package needs. `pyee` (required by `playwright`) shipped unpinned until 2026-10, so every image took whichever 12.x pip found that day (playwright bounds it to `>=12,<13`) and no audit ever saw it. CI's "Check the Python pins" step now fails when a pin is missing; the clean-venv comparison in "Running a triage" finds the version to pin.
- **One bad update holds back a grouped Dependabot PR.** If the monthly group fails CI, find the package that broke it, add it to that group's `exclude-patterns` in `.github/dependabot.yml`, and upgrade it on its own branch. Dependabot then regenerates the group without it, and the package gets its own PR. Do not use `ignore` for this: a plain `ignore` entry also stops that package's security PRs. (`ignore` with `update-types`, for example to stay on a Django LTS, only skips version updates.)
- **A class the pipeline adds BEFORE sanitizing must be in `ALLOWED_CLASSES`.** Anything else is stripped as if an author wrote it (classes the styling passes add afterwards need nothing). Never put `class` back in `ALLOWED_HTML_ATTRS` either: nh3 panics when `class` is both a plain attribute and in `allowed_classes`, and the panic is a `BaseException` that `process_markdown`'s `except Exception` fallback does not catch, so every render would fail with a 500. `test_sanitizers.py` fails first.
- **Never allowlist `rel` or `target` on links.** `process_markdown`'s link pass appends its own `target`/`rel` after the sanitizer runs, and the first copy of a duplicated attribute wins, so an author-supplied one would override the site's (that is how `rel="opener"` got through). `link_rel=None` stops nh3 adding a second `rel` of its own. Allowlisting `rel` again would bring the bug straight back with no error (nh3 only raises when `rel` is allowlisted while it is also adding its own); `test_external_links_always_get_the_sites_own_target_and_rel` catches it.
- **The pip-audit gate can turn an unrelated PR red.** It checks the advisory database at run time, so a newly published advisory (or the database being unreachable) fails CI on a PR that changed no dependencies. That is the point: bump the package, or, if no usable fix exists, add `--ignore-vuln <ID>` in `tests.yml` with a row here saying why. Do not remove the step to get a PR through.
- **`npm audit` overstates production risk.** It reports every advisory as a production one, because the Tailwind toolchain sits in `dependencies` rather than `devDependencies`. None of it is served. Check the "Where each dependency tree runs" table before treating an npm critical as a site incident, and never answer one with `npm audit fix --force`: it installs versions outside the declared `package.json` ranges (majors included), and the rebuilt CSS is then no longer guaranteed identical.
- **Log filter blast radius.** `SuppressBotMagnet405` is attached at the handler level, so any logger that routes through `console` benefits. The filter only matches the exact `Method Not Allowed (...): /<bot-magnet-path>` pattern, so other warnings are unaffected — but if you add a new logger that legitimately emits that exact string for a real endpoint, it will be swallowed. Update `BOT_MAGNET_405_PATTERN` if a new bot-magnet path needs adding.
