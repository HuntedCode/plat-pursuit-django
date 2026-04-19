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

Rate limits use `django-ratelimit` with `key='user'` and `block=True` (returns 429 on excess).

### Rate Limit Coverage

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

## CORS Policy

CORS is configured via `django-cors-headers`. Production origins come from the `CORS_ALLOWED_ORIGINS` environment variable. Development origins (`localhost:8081`, `localhost:19006`) are only added when `DEBUG=True`.

## Easter Egg Security

Easter egg probability rolls are performed server-side. The client cannot determine outcomes. See [Easter Eggs](../features/easter-eggs.md) for the full architecture.

## Admin Log Privacy

Admin action logs use `user_id` instead of email addresses to avoid PII in log storage. This applies to notification sending, subscription admin actions, and fundraiser claim management.

## Gotchas and Pitfalls

- **CSP and new CDN resources.** If you add a new external script, stylesheet, font, or image source, you must update the CSP directives in settings.py or the resource will be blocked. Google AdSense requires multiple Google domains across script-src, img-src, frame-src, and connect-src.
- **Rate limit key.** All rate limits use `key='user'` which requires authentication. For unauthenticated endpoints, use `key='ip'` instead.
- **CORS localhost leak.** Before this hardening, localhost origins were unconditionally added to production CORS. Always gate development-only origins behind `if DEBUG`.
- **`throttle_classes = []` disables DRF throttling.** If you see this on a view, it's intentionally removing DRF's built-in throttling. Make sure `django-ratelimit` is used as a replacement, not that throttling is simply absent.
