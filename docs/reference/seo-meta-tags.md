# SEO & Meta Tags

> **Strategy of record: [docs/design/seo-strategy.md](../design/seo-strategy.md)** (the five
> structural decisions, 2026-08-23). This reference describes the MACHINERY; the strategy doc
> says why. Brought current with SEO Lanes 0-1 on 2026-08-23 -- it had drifted through four URL
> migrations and a sitemap rework.


The SEO system provides dynamic meta tags, structured data (JSON-LD), sitemaps, and robots directives across all pages. Built on Django's template block system with no external packages.

## Architecture Overview

All SEO infrastructure lives in `templates/base.html` using template blocks that child templates can override. Views pass context variables (`seo_description`, `seo_title`) that automatically populate meta description, Open Graph, and Twitter Card tags. JSON-LD structured data is generated via custom template tags in `core/templatetags/seo_tags.py`.

The design prioritizes DRY: views set a single `seo_description` string and it flows into three different meta tags (meta description, OG description, Twitter description). Individual blocks can still be overridden when OG needs to differ from the meta tag.

## File Map

| File | Purpose |
|------|---------|
| `templates/base.html` | All SEO meta tag blocks, JSON-LD integration, favicon links |
| `core/templatetags/seo_tags.py` | JSON-LD template tags (Organization, WebSite, BreadcrumbList, VideoGame, ProfilePage) |
| `core/sitemaps.py` | Sitemap classes for all content types |
| `plat_pursuit/urls.py` | Sitemap registration at `/sitemap.xml` |
| `static/robots.txt` | Robots directives (served via `RobotsTxtView`) |
| `plat_pursuit/middleware.py` | `BotCanonicalRedirectMiddleware` enforces crawler policy for bots that ignore `robots.txt`; `CloudflareOriginGuardMiddleware` bounces direct-origin scrapers back through Cloudflare |

## SEO Block System (base.html)

### Available Blocks

| Block | Default Behavior | When to Override |
|-------|-----------------|-----------------|
| `title` | Falls back to `{{ title }}` context var or "Platinum Pursuit". Auto-appends " - Platinum Pursuit" suffix. | Always set for named pages |
| `meta_description` | Uses `{{ seo_description }}` context var, falls back to site tagline | Set via `seo_description` in views, or override block for static pages |
| `robots` | `index, follow` | Override with `noindex, nofollow` for auth/personal/edit pages |
| `canonical_url` | scheme://host/PATH (querystring STRIPPED, Lane 0) | Override when another URL is the canonical; both game detail pages set an explicit view-computed self-canonical (never request.path, which would mint per-viewer canonicals on profile-scoped variants); condition must live INSIDE the block with `{{ block.super }}` -- a block wrapped in `{% if %}` overrides unconditionally |
| `og_title` | Uses `{{ seo_title }}` or `{{ title }}` context var | Only if OG title should differ from page title |
| `og_description` | Uses `{{ seo_description }}` context var | Only if OG description should differ from meta description |
| `og_type` | `website` | Override: `profile` for profile pages, `article` for guides |
| `og_image` | Falls back to site logo | Override for pages with dynamic images |
| `twitter_card_type` | `summary` | Use `summary_large_image` for pages with large thumbnails |
| `twitter_title` | Same as OG title | Rarely needs separate override |
| `twitter_description` | Same as OG description | Rarely needs separate override |
| `twitter_image` | Same as OG image | Rarely needs separate override |

### Context Variable Pattern

Instead of overriding multiple blocks per page, views set a single context variable:

```python
# In get_context_data():
context['seo_description'] = f"{game.title_name} on {game.platforms_display}. ..."
```

This automatically populates:
- `<meta name="description">`
- `<meta property="og:description">`
- `<meta name="twitter:description">`

Similarly, `seo_title` populates OG and Twitter title tags.

## JSON-LD Structured Data

### Template Tags (`core/templatetags/seo_tags.py`)

Load with `{% load seo_tags %}`.

| Tag | Usage | Output |
|-----|-------|--------|
| `{% jsonld_organization %}` | All pages (in base.html) | Organization schema with name, URL, logo |
| `{% jsonld_website request %}` | Homepage only | WebSite schema with SearchAction |
| `{% jsonld_breadcrumbs breadcrumb request %}` | Pages with breadcrumb context (in base.html) | BreadcrumbList from existing breadcrumb data |
| `{% jsonld_game game concept request %}` | Game detail page | VideoGame schema with platforms, publisher, genres |
| `{% jsonld_profile profile request %}` | Profile detail page | ProfilePage schema with username, avatar |

### Adding a New Schema

1. Add a new `@register.simple_tag` function in `seo_tags.py`
2. Build the schema dict and return `_render_jsonld(data)`
3. Add `{% load seo_tags %}` to the template (if not already loaded)
4. Call the tag in `{% block extra_head %}` or inline

## Sitemaps (`core/sitemaps.py`)

Registered (see `plat_pursuit/urls.py` -- registration is explicit, and so is the index):

| Class | Content | Priority | Frequency |
|-------|---------|----------|-----------|
| `StaticViewSitemap` | Homepage, copy pages, browse hubs | 0.8 | weekly |
| `GameSitemap` | Game pages only (`game_page_canonicals()` window election), shovelware excluded | 0.6 | weekly |
| `ListSitemap` (`game_lists`) | Every non-shovelware trophy list at its self-canonical `/games/<np>/` URL (no election -- disjoint from GameSitemap, which is concept-bearing Game pages only) | 0.5 | weekly |
| `ProfileSitemap` | Quality-floored profiles: public history + trophies > 0; `lastmod` = `last_synced` | 0.5 | daily |
| `BadgeSitemap` | `BadgeSeries` with a live `GroupBadge` edition (the set BadgeDetailView serves) | 0.6 | weekly |

Withdrawn/unregistered: `RoadmapSitemap` (Roadmaps hidden, no return promised -- 2026-08-23),
`GameListSitemap` (Lists hidden). The old `GuideSitemap` and a documented-but-never-built
`ChallengeSitemap` are gone with their systems.

The index at `/sitemap.xml` and per-section pages are wired EXPLICITLY in urls.py with
`limit = 5000` per page (not Django's 50,000 default -- sized for origin cost). Every
`get_latest_lastmod` is an ORDER BY ... LIMIT 1 (the May-2026 OOM fix); keep it that way.

### Adding a New Sitemap

1. Create a new `Sitemap` subclass in `core/sitemaps.py`
2. Define `items()`, `location()`, and optionally `lastmod()`
3. Register it in the `sitemaps` dict in `plat_pursuit/urls.py`

## Access Policy: Anonymous Profile-Scoped Views

Profile-scoped detail URLs require authentication. They're the most expensive render paths on the site (per-profile `EarnedTrophy` queries, milestone dicts, etc.) and bot fan-out against them has been the primary driver of container-level OOM crashes. Rather than serve them to anyone who shows up, we gate them at view dispatch.

| Anonymous request | Response |
|-------------------|----------|
| `/games/<np_id>/<username>/` | 302 → `/games/<np_id>/?from_profile=<username>` |
| `/my-pursuit/badges/<slug>/<username>/` | 302 → `/badges/<slug>/?from_profile=<username>` |
| `/badges/<slug>/<username>/` (legacy) | Existing legacy 301 chain → then gated same way |

Logic lives in `GameDetailView.dispatch` and `BadgeDetailView.dispatch`. The `from_profile` query param surfaces a dismissible sign-up banner on the canonical page via `templates/partials/anon_profile_banner.html`, turning the moment into a soft sign-up pitch instead of a wall — the visitor still gets a useful page about the game or badge they wanted to see.

### Why this shape

- **Canonical page, not login form.** Redirecting to `/accounts/login/` is a wall. Redirecting to the canonical page means the visitor still sees something useful and can dismiss the banner if they're just browsing.
- **302, not 301.** The redirect is conditional on auth state, which changes at runtime. A 301 would tell caches "this URL permanently lives at the canonical path" — wrong when signed-in users should hit the profile-scoped page.
- **Dismissible banner.** Session-storage flag so it doesn't re-appear on every page load for someone who's decided they're just browsing. Resets with the session.
- **OG tags still come from the canonical page.** The canonical page's `og:*` tags are profile-independent (see the Bot Canonical Redirect section for why), so shared social-media previews render the game/badge info either way.

### Gotchas

- **Auth check lives in `dispatch`, not a mixin.** This is deliberate — mixins stack and reorder oddly with `DetailView`; keeping the redirect inline in each view's dispatch keeps the reading path linear.
- **Query string is preserved.** `?tier=3` on a profile-scoped badge URL flows through the redirect so the canonical page honors the same sub-selection.
- **Logged-in users hit profile-scoped paths normally.** No redirect, no banner — they see the full profile-scoped render.

## Crawler Policy: Bot Canonical Redirect

`BotCanonicalRedirectMiddleware` (in `plat_pursuit/middleware.py`, wired early in `MIDDLEWARE` right after WhiteNoise) 301-redirects known crawler requests for profile-scoped URL variants to their canonical counterparts:

| Bot request | Canonical target |
|-------------|------------------|
| `/games/<np_id>/<username>/` | `/games/<np_id>/` |
| `/my-pursuit/badges/<slug>/<username>/` (legacy prefix) | `/badges/<slug>/` |
| `/badges/<slug>/<username>/` | `/badges/<slug>/` |
| `/achievements/badges/<slug>/<username>/` (legacy prefix) | `/badges/<slug>/` |

Query strings (e.g. `?tier=3` on badge detail) are preserved through the redirect. Legacy badge prefixes are caught directly rather than falling through the non-bot 301 chain in `plat_pursuit/urls.py`, which avoids a two-hop redirect when crawlers follow old backlinks.

### Why this exists

`static/robots.txt` used to `Disallow` `/games/*/*`-style patterns for the profile-scoped variants -- REMOVED in SEO Lane 0 (2026-08-23): robots `*` matches zero-or-more characters, so those rules also blocked every canonical detail page (there is no robots pattern for "exactly two segments"). The variants' defense is this middleware plus the anon gate; robots handles only the profile `?`-permutations and fragment endpoints. However, some crawlers (Meta's `meta-webindexer` in particular) ignore `Disallow` rules, and parallel fan-out of expensive profile-scoped renders has caused origin memory spikes and worker saturation. This middleware enforces the `robots.txt` intent for those crawlers at request-entry, before any session/auth/ORM work runs.

### Gotchas

- **Only bot UAs are matched.** Real users hitting profile-scoped URLs get the full page as normal. The UA list lives in `_BOT_UA_RE` in `plat_pursuit/middleware.py` and may need occasional updates as new aggressive crawlers appear.
- **UA regex failure mode is graceful.** If a new bot slips through the list, it just hits the full page (same as pre-middleware behavior). No false throttling.
- **Not cloaking.** Google explicitly endorses canonical redirects for duplicate content. This is the textbook solution.
- **Do not extend to pages without a canonical non-profile variant.** `/profiles/<user>/*` has no canonical strip-to; the profile IS the page. The current regex correctly ignores those paths.
- **That exclusion is exactly how the 2026-08-09 outage started.** Having no redirect target is a reason this middleware cannot help the profile page, NOT a reason the page is safe. It fell through every guard and was the first URL to time out. Profiles are now covered by the origin guard below, plus a render-cost gate in the view (see [Anonymous Render Cost](#anonymous-render-cost)). When adding a new expensive page, ask which of the three layers covers it; "none, but it has no canonical variant" is not an answer.
- **Tests live in `tests/engine/test_cloudflare_guard_paths.py`.** Add cases here when extending either regex.

## Crawler Policy: Cloudflare Origin Guard

`CloudflareOriginGuardMiddleware` (in `plat_pursuit/middleware.py`, wired just before `BotCanonicalRedirectMiddleware`) 302-redirects direct-origin requests for the same profile-scoped paths back through Cloudflare's public hostname. It's a defense against scrapers that cached the origin IP (e.g. during the window when the Render `*.onrender.com` subdomain was publicly resolvable) and continue connecting direct-origin while spoofing `Host: platpursuit.com`.

### How the guard decides

Cloudflare stamps every proxied request with a `CF-Ray` header. If that header is missing on a guarded path, the request reached Django without traversing the proxy. The middleware bounces those to `https://platpursuit.com<path>` with a 302 so the next hop re-enters through Cloudflare, where Bot Fight Mode and WAF rules can evaluate it.

### Scope

Deliberately narrow — only the profile-scoped patterns covered by `_CLOUDFLARE_GUARDED_PATH_RE`:

| Path pattern | Behavior without `CF-Ray` |
|--------------|---------------------------|
| `/games/<np_id>/<username>/` | 302 → `https://platpursuit.com/<path>` |
| `/badges/<slug>/<username>/` (+ legacy prefixes) | 302 → `https://platpursuit.com/<path>` |
| `/badges/<slug>/<username>/` (legacy) | 302 → `https://platpursuit.com/<path>` |
| `/achievements/badges/<slug>/<username>/` (legacy) | 302 → `https://platpursuit.com/<path>` |
| `/hunters/<username>/` (+ sub-pages; legacy /profiles/ spellings too) | 302 → `https://platpursuit.com/<path>` |
| Everything else (`/`, static, browse, `/hunters/`, etc.) | Unaffected — passes through |

The narrow scope is intentional: Render's internal health checks hit `/` without a `CF-Ray` header, and a broader guard would trip them and cause false restarts. The profile LIST page (`/hunters/`, no trailing username) is deliberately outside the guard: it is cheap and paginated, and it is the page a legitimate crawler should be walking.

Profiles joined this list after 2026-08-09. Because the profile page has no canonical variant to redirect to, the origin guard is the ONLY request-entry protection it can have — everything else has to come from making the render itself cheap.

## Anonymous Render Cost

Guards keep crawlers off a page; they do not make the page affordable when a crawler gets through one (a spoofed UA defeats the bot rule, and only direct-origin hits trip the CF guard). The profile page therefore also gates its two unbounded costs on `request.user.is_authenticated`, in `ProfileDetailView.get_context_data`:

| Work | Anonymous | Authenticated |
|------|-----------|---------------|
| Header stats (incl. all four Platinum Highlight cards) | ✅ runs | ✅ runs |
| Games / trophies tab (paginated, 50/page) | ✅ runs | ✅ runs |
| Badges tab (**not** paginated — see gotcha) | ✅ runs | ✅ runs |

Showcases are deliberately **not** gated: a shared profile link is mostly opened logged-out, which is the audience the customization exists for, and every remaining provider is bounded by config or by a small owned table (≤20 selected platinums, ≤6 game ids, ≤5 badges, ≤6 titles, 6 date-indexed platinums). The one provider that was *not* bounded — Rarest Trophies, which ranked the profile's entire earned set on a joined column — was **removed outright** (migration `0275`) rather than gated, because its cost came from "rank everything I own" and would have remained a liability for signed-in views of large profiles.

The timeline is still gated because it is cached per profile, which means a crawler enumerating distinct profiles has a 0% hit rate **by construction** — per-entity caching cannot protect an enumerable URL space, only gating can.

### Gotchas

- **Gate before the work, not around its output.** This is the same rule as the premium-preview pattern in CLAUDE.md. A version that computes the data and hides it in the template looks correct and still takes the site down. `tests/engine/test_anon_profile_render.py` asserts on the CALL, not the context value, for exactly this reason.
- **Prefer deleting an unbounded feature to gating it.** Gating only moves the cost to signed-in traffic. If a feature's cost scales with the account rather than with what it displays, that is a design problem, not an access-control problem.
- **The four Platinum Highlight cards are deliberately NOT gated.** They render a "None" empty state when absent, so skipping them for anonymous visitors would *misreport* the profile rather than hide a section. They are also cheap (two denormed FKs plus two lookups bounded by the profile's `ProfileGame` rows).
- **Both the guard and `robots.txt` hold HARDCODED paths.** Profiles moved from `/community/profiles/`
  to `/profiles/` in 2026-08, and neither of these follows a rename the way `{% url %}` does -- each
  carries both spellings while the 301s live. Moving a guarded surface means editing both in the same
  change, or the protection silently stops matching anything.
- **Profiles stay indexable.** `robots.txt` disallows only the query-string permutations (`/profiles/*/?*` — the `?tab=` / `?page=` / `?sort=` axes that multiply into an unbounded crawl space). The canonical profile page keeps its search and share value; profiles are the free floor of the product. Note the `/` before `?`: `*` matches the empty string, so the shorter `/profiles/*?*` would also block the profile *index's* pagination.
- **`?tab=badges` is still unpaginated.** `_build_badges_tab_context` takes no page argument: it builds an OR-chain `Q()` over every earned series plus a full `UserBadgeProgress` scan for the profile, then groups in Python. It is bounded by the *badge catalogue* (hundreds of series), not by the profile's trophy count, so it is nowhere near the 250K-row class this gate was built for — but it is the largest remaining anonymous cost on this page and the obvious next thing to paginate.

### Diagnostics

Every caught bypass emits an INFO-level log line with the grep-friendly prefix `CF_BYPASS_BLOCKED`:

```
INFO 2026-XX-XX HH:MM:SS,NNN plat_pursuit.middleware CF_BYPASS_BLOCKED path=... ip=... ua='...'
```

These flow through the standard `plat_pursuit` logger → console handler → stdout → Render log viewer. Search for `CF_BYPASS_BLOCKED` in Render logs to quantify how much direct-origin traffic is being funneled back through the proxy.

### Gotchas

- **302, not 301.** Response is intentionally not permanent. Whether a given request belongs behind the guard depends on runtime CF-Ray presence, not a URL property, so caching the redirect would be wrong.
- **Order matters.** Wired ahead of `BotCanonicalRedirectMiddleware` so direct-IP hits get funneled back through CF before the bot-UA canonical redirect evaluates — otherwise known-bot direct-origin hits would 301 to the non-profile canonical (which isn't guarded) and never pass through CF at all.
- **Depends on Cloudflare fronting real traffic.** If CF is bypassed for legitimate users (e.g. DNS misconfiguration, gray-cloud record), the guard will redirect them too. Verify CF is proxying with `curl -sI https://platpursuit.com/ | grep cf-ray` before assuming the guard is safe.
- **Tests live in `plat_pursuit/tests/test_middleware.py`** (`CloudflareOriginGuardMiddlewareTests` class).

## Key Flows

### Page Renders with SEO Data

1. View's `get_context_data()` sets `seo_description` (and optionally `seo_title`)
2. `base.html` renders meta tags using the context variables
3. JSON-LD Organization and BreadcrumbList render automatically (if breadcrumb context exists)
4. Page-specific JSON-LD renders via `{% block extra_head %}`

### Adding SEO to a New Page

1. Set `{% block title %}Page Name{% endblock %}` in the template
2. Add `context['seo_description'] = "..."` in the view's `get_context_data()`
3. If auth-required or personal: add `{% block robots %}noindex, nofollow{% endblock %}`
4. If the page has a dynamic image: override `{% block og_image %}`

## Gotchas and Pitfalls

- **Title double-suffix**: `base.html` auto-appends " - Platinum Pursuit" to `{% block title %}`. Never include "- Platinum Pursuit" inside the block content or it will appear twice.
- **seo_description max length**: Google typically shows 155-160 characters for meta descriptions. Keep `seo_description` values concise.
- **OG image requirements**: Facebook recommends 1200x630px images. The default logo.png fallback is small (128x128). For best social sharing, override `og_image` with a larger image.
- **noindex on personal pages**: Auth-required pages (dashboard, my_*, settings) have `noindex` set. If you create a new personal page, add the robots block.
- **Breadcrumb structure**: JSON-LD breadcrumbs expect `[{'text': 'Name', 'url': '/path/'}, ...]`. The last item should omit `url` (it auto-uses the current page URL).
- **Sitemap model querysets**: Don't add `[:N]` limits to sitemap querysets. Django handles pagination natively.
- **Template tag loading**: `seo_tags` is loaded in `base.html`, but child templates that use page-specific tags (like `jsonld_game`) need to load it again: `{% load seo_tags %}`.

## Related Docs

- [Data Model](../architecture/data-model.md): Game, Profile, Badge, Challenge models (Checklist tables retained in schema but the system was retired)
- [JS Utilities](js-utilities.md): Frontend utilities

## Lane 1 behaviors (2026-08-23)

- **Page-identity election**: `canonical_election_order()` (trophies/managers.py) is THE
  ordering inside `GameQuerySet.game_page_canonicals()` (GameSitemap's window, shovelware and
  np-less rows excluded). Each page's own view-computed `page_canonical_url` shares the
  `Concept.game_page_url` routing with it, so sitemap and canonical cannot drift -- pinned by
  the invariant test over BOTH GameSitemap and ListSitemap. (The old
  `concept_canonicals()`/`Game.canonical_sibling()` pair was deleted with the Games/Trophy
  Lists IA; list pages are SELF-canonical since the slim-down.)
- **The games hub returns 200 bare**: anon hits bind the platform defaults into the form and
  the template history.replaceState()s them into the URL (no redirect); signed-in hunters with
  saved browse defaults keep their personalization redirect.
- **Profile casing**: `/hunters/<Name>/` (+ the day pages) 301 to the lowercase stored form.
