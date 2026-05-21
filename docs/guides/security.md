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
| `script-src` | `'self'`, `'unsafe-inline'`, `cdn.jsdelivr.net`, Google AdSense domains |
| `style-src` | `'self'`, `'unsafe-inline'`, `fonts.googleapis.com` |
| `font-src` | `'self'`, `fonts.gstatic.com` |
| `img-src` | `'self'`, `data:`, PSN domains (http + https), `*.s3.amazonaws.com`, Google AdSense domains |
| `frame-src` | `'self'`, Google AdSense iframe domains |
| `connect-src` | `'self'`, `cdn.jsdelivr.net` (source maps), Google AdSense domains |
| `frame-ancestors` | `'none'` |

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

## Gotchas and Pitfalls

- **CSP and new CDN resources.** If you add a new external script, stylesheet, font, or image source, you must update the CSP directives in settings.py or the resource will be blocked. Google AdSense requires multiple Google domains across script-src, img-src, frame-src, and connect-src.
- **Rate limit key.** All `django-ratelimit` decorators use `key='user'` which requires authentication. For unauthenticated endpoints, use `key='ip'` instead. Allauth's `ACCOUNT_RATE_LIMITS` uses its own format (`'<count>/<period>[/<scope>]'` where scope is `ip` or `key`).
- **CORS localhost leak.** Before this hardening, localhost origins were unconditionally added to production CORS. Always gate development-only origins behind `if DEBUG`.
- **`throttle_classes = []` disables DRF throttling.** If you see this on a view, it's intentionally removing DRF's built-in throttling. Make sure `django-ratelimit` is used as a replacement, not that throttling is simply absent.
- **Mobile signup is protected by a different rate limit and skips the honeypot.** `MobileSignupView` in `api/mobile_auth_views.py` calls allauth's base `SignupForm` directly rather than going through `SignupView`, so neither `ACCOUNT_RATE_LIMITS['signup']` nor the honeypot in `CustomUserCreationForm` apply. The view IS protected by its own `@ratelimit(key='ip', rate='3/m', method='POST', block=True)` decorator (django-ratelimit, returns 429 on excess). Web signup uses the allauth path (`5/m/ip` + honeypot); mobile uses django-ratelimit (`3/m/ip`, no honeypot). Both are throttled, just via different mechanisms.
- **Log filter blast radius.** `SuppressBotMagnet405` is attached at the handler level, so any logger that routes through `console` benefits. The filter only matches the exact `Method Not Allowed (...): /<bot-magnet-path>` pattern, so other warnings are unaffected — but if you add a new logger that legitimately emits that exact string for a real endpoint, it will be swallowed. Update `BOT_MAGNET_405_PATTERN` if a new bot-magnet path needs adding.
