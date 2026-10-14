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
| Verify npm | `npm run build`, then confirm `static/css/output.css` hashes the same as before the fix; `npm run lint`; if `@parcel/watcher` moved, smoke-test `npm run watch` with a real content change |
| Python audit | `pip install -r requirements-dev.txt` (it pins `pip-audit`), then `pip-audit -r requirements.txt --no-deps --disable-pip`. Every transitive dependency is pinned, so `--no-deps` audits exactly what ships. CI runs the same command as a gate ("Audit Python dependencies" in `.github/workflows/tests.yml`); to accept an advisory with no usable fix, add `--ignore-vuln <ID>` there and a row here saying why |
| Python pins | After a bump, install `requirements.txt` into a clean venv and compare `pip freeze` with the pins: every installed package must be pinned (or the audit cannot see it) and every pin installed |

### 2026-10 triage

**npm: 10 advisories (1 critical, 8 high, 1 moderate), all build-only, all fixed by `npm audit fix` (lockfile only, no direct dependency changed version).** Rebuilt CSS was byte-identical.

| Package | Severity | Pulled in by | Was, now |
|---------|----------|--------------|----------|
| tar | critical | `@tailwindcss/oxide` | 7.5.2, 7.5.22 |
| postcss | high | direct (unused by the Tailwind v4 CLI) | 8.5.6, 8.5.30 |
| braces, micromatch | high | `@parcel/watcher` (watch mode) | removed (watcher 2.6.0 dropped them) |
| picomatch | high | `@parcel/watcher` | 2.3.1, 4.0.7 (the range watcher 2.6.0 asks for) |
| browserslist | high | `autoprefixer` | 4.26.3, 4.29.3 |
| nanoid | high | `postcss` | 3.3.11, 3.3.20 |
| source-map-js | high | `postcss`, `@tailwindcss/node` | 1.2.1, 1.2.2 |
| baseline-browser-mapping | moderate | `browserslist` | 2.8.16, 2.11.28 |

The only `npm ci` deprecation warning is `eslint@9.39.5` (end of support). Clearing it means ESLint 10, a major upgrade, so it is deferred.

**Python: 96 unique advisories across 15 packages in `requirements.txt`, all cleared by 2026-10-14.** Django and Pillow, the two High rows, went first (see [Image Uploads](#image-uploads) for the Pillow follow-through); a follow-up PR took every other row. `pip-audit` now reports no known vulnerabilities, and CI fails on any new one.

| Package | Pinned | Clears all | Why it matters here | Priority |
|---------|--------|------------|---------------------|----------|
| Django | 5.2.7 | 5.2.17 (same 5.2 LTS line) | 30 advisories: SQL injection in `FilteredRelation` / `order_by` column aliases and `_connector` dict expansion, ASGI and multipart DoS, cache-middleware response leaks, `get_signed_cookie` salt collision | High (fixed: 5.2.17) |
| Pillow | 12.0.0 | 12.3.0 | 19 advisories, several heap out-of-bounds writes / memory corruption (PSD, EPS, JPEG2000, GD, PCF/BDF fonts, coordinate APIs). `trophies/image_utils.py` called `Image.open()` on user uploads with no `formats=` allowlist, so those parsers were reachable | High (fixed: 12.3.0, and uploads now reach only the allowed decoders) |
| djangorestframework | 3.16.1 | 3.17.2 | `request.data` parsing can bypass Django's upload memory limits; AdminRenderer data leak on 400s | Medium (fixed: 3.17.2) |
| urllib3 | 2.5.0 | 2.8.0 | Decompression bombs in streaming, cross-origin redirect header leak, proxy TLS settings ignored. Affects outbound calls (PSN, IGDB, Discord, S3) | Medium (fixed: 2.8.0) |
| cryptography | 46.0.3 | 50.0.0 | 46.0.7 (patch) clears three; the bundled-OpenSSL advisory and the rest need 48 to 50 (major) | Medium (fixed: 50.0.2; only `sendgrid` requires it, `>=45.0.6`, and nothing calls it) |
| bleach | 6.2.0 | 6.4.0 | Disallowed URI schemes survive in `href` when `a` + `href` are allowed, which `checklist_service.py` does. The advisory rates it as not directly exploitable in browsers | Medium (fixed: 6.4.0, but bleach's changelog says it is no longer maintained and future advisories will get no fix; replacing it with `nh3` is a follow-up) |
| tornado | 6.5.2 | 6.5.9 | 14 advisories, but nothing imports tornado and no installed package requires it. Remove the pin rather than upgrade it | Low (removed) |
| black | 25.9.0 | 26.3.1 | A formatter pinned in the production requirements; belongs in `requirements-dev.txt` | Low (moved to dev as 26.10.1; its helper packages left the production pins with it and are resolved by pip in dev, since 26.x needs newer ones) |
| sqlparse | 0.5.3 | 0.6.0 | DoS on crafted SQL; Django uses it for SQL formatting, not on request input | Low (fixed: 0.6.0) |
| django-allauth | 65.13.1 | 65.14.1 | Open redirect only with SAML IdP-initiated SSO, which is not configured | Low (fixed: 65.14.1) |
| Werkzeug | 3.1.4 | 3.1.9 | `safe_join` Windows device names; only pulled in by `sendgrid`, never serves files | Low (fixed: 3.1.9) |
| Markdown | 3.7 | 3.8.1 | Crash on malformed HTML-like input; renders staff-authored broadcast emails only | Low (fixed: 3.8.1; with the app's `extra`, `nl2br`, `sane_lists` extensions it renders sample broadcasts byte-identically to 3.7) |
| requests, idna, python-dotenv | 2.32.5, 3.11, 1.2.1 | 2.33.0, 3.15, 1.2.2 | Temp-file name in a zip helper, idna DoS on long input, `.env` symlink follow (local only) | Low (fixed) |

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
- **Rate limit key.** All `django-ratelimit` decorators use `key='user'` which requires authentication. For unauthenticated endpoints, use `key='ip'` instead. Allauth's `ACCOUNT_RATE_LIMITS` uses its own format (`'<count>/<period>[/<scope>]'` where scope is `ip` or `key`).
- **CORS localhost leak.** Before this hardening, localhost origins were unconditionally added to production CORS. Always gate development-only origins behind `if DEBUG`.
- **`throttle_classes = []` disables DRF throttling.** If you see this on a view, it's intentionally removing DRF's built-in throttling. Make sure `django-ratelimit` is used as a replacement, not that throttling is simply absent.
- **Mobile signup is protected by a different rate limit and skips the honeypot.** `MobileSignupView` in `api/mobile_auth_views.py` calls allauth's base `SignupForm` directly rather than going through `SignupView`, so neither `ACCOUNT_RATE_LIMITS['signup']` nor the honeypot in `CustomUserCreationForm` apply. The view IS protected by its own `@ratelimit(key='ip', rate='3/m', method='POST', block=True)` decorator (django-ratelimit, returns 429 on excess). Web signup uses the allauth path (`5/m/ip` + honeypot); mobile uses django-ratelimit (`3/m/ip`, no honeypot). Both are throttled, just via different mechanisms.
- **Never store a user's image bytes as uploaded, and never let them pick the extension.** A new upload path should go through `process_roadmap_image` or `optimize_image` (or reuse `_flatten_to_rgb` and `_as_upload`), and treat their `ValidationError` as "refuse the upload". Falling back to the raw file "so nothing is blocked" is exactly what leaked GPS data.
- **Never `Image.open()` a user upload directly.** Use `_open_upload` (or the helpers that call it) in `trophies/image_utils.py`, or pass `formats=ALLOWED_IMAGE_FORMATS` as the card renderer does. A bare `Image.open()` runs every Pillow parser again, and a format check afterwards is too late: the parser has already run. Django's `ImageField` form validation also opens files with every decoder, but it is only reached from the staff-only admin.
- **An unpinned package is invisible to the Python audit.** `pip-audit --no-deps` checks only what `requirements.txt` names, but pip still installs whatever a pinned package needs. `pyee` (required by `playwright`) shipped unpinned until 2026-10, so every image took whichever 12.x pip found that day (playwright bounds it to `>=12,<13`) and no audit ever saw it. After any bump, run the clean-venv comparison in "Running a triage".
- **The pip-audit gate can turn an unrelated PR red.** It checks the advisory database at run time, so a newly published advisory (or the database being unreachable) fails CI on a PR that changed no dependencies. That is the point: bump the package, or, if no usable fix exists, add `--ignore-vuln <ID>` in `tests.yml` with a row here saying why. Do not remove the step to get a PR through.
- **`npm audit` overstates production risk.** It reports every advisory as a production one, because the Tailwind toolchain sits in `dependencies` rather than `devDependencies`. None of it is served. Check the "Where each dependency tree runs" table before treating an npm critical as a site incident, and never answer one with `npm audit fix --force`: it installs versions outside the declared `package.json` ranges (majors included), and the rebuilt CSS is then no longer guaranteed identical.
- **Log filter blast radius.** `SuppressBotMagnet405` is attached at the handler level, so any logger that routes through `console` benefits. The filter only matches the exact `Method Not Allowed (...): /<bot-magnet-path>` pattern, so other warnings are unaffected — but if you add a new logger that legitimately emits that exact string for a real endpoint, it will be swallowed. Update `BOT_MAGNET_405_PATTERN` if a new bot-magnet path needs adding.
