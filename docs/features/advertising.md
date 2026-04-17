# Advertising

Google AdSense integration with Funding Choices (CMP) for consent, Consent Mode v2 for default-deny signaling, and premium-tier + path-based ad suppression. One ad provider, one CMP, per-page slot IDs for analytics visibility.

## Architecture Overview

All AdSense code lives in two places: the loader + CMP + sidebars + mobile banner in `templates/base.html`, and the in-content slot partial at `templates/partials/ad_unit.html` that pages include where they want an inline ad. Whether ads render at all is gated by the `ads` context processor in `plat_pursuit/context_processors.py`, which sets `ADSENSE_ENABLED` to False for premium users, auth/admin/staff/api paths, and the fundraiser page.

Ad targeting quality depends on the AdSense crawler understanding what each page is about. That's handled by the SEO infrastructure (meta tags + VideoGame JSON-LD on game detail pages, narrow-scoped Open Graph overrides on other detail pages). See [SEO & Meta Tags](../reference/seo-meta-tags.md) for the full meta tag inventory.

Consent before ad personalization is the law in EU/UK/EEA and is AdSense's own policy requirement as of 2024. Consent Mode v2 defaults ad_storage/ad_user_data/ad_personalization/analytics_storage all to `denied`, and the Funding Choices CMP upgrades those flags to `granted` on user accept. The default-deny runs as the first script in `<head>` so no personalized signals are sent before consent is resolved.

## File Map

| File | Purpose |
|------|---------|
| `templates/base.html` (head) | Consent Mode v2 default-deny, Funding Choices CMP loader + signal iframe, AdSense loader |
| `templates/base.html` (sidebars + mobile banner) | Left/right sticky desktop rails (slots `5958570818`, `1688153315`) and dismissible 320x50 mobile banner (slot `9347573919`) |
| `templates/partials/ad_unit.html` | Reusable horizontal in-content slot, takes `ad_slot` parameter, self-hides on unfilled via MutationObserver |
| `plat_pursuit/context_processors.py` (`ads`) | Gates `ADSENSE_ENABLED` by premium tier and path prefix |
| `plat_pursuit/settings.py` (CSP directives) | Allows AdSense + Funding Choices domains in script-src / frame-src / img-src |
| `core/templatetags/seo_tags.py` (`jsonld_game`) | VideoGame schema.org structured data on game detail, helps AdSense categorize content |

## Slot Inventory

Each slot ID is a separate ad unit in AdSense, which enables per-page revenue reporting. Sidebars and mobile banner live in `base.html` and render on most pages. In-content slots are added explicitly with `{% include 'partials/ad_unit.html' with ad_slot="..." %}`.

| Slot ID | Name | Location | Template |
|---------|------|----------|----------|
| `5958570818` | sidebar-left | Desktop left rail, sticky | `templates/base.html` |
| `1688153315` | sidebar-right | Desktop right rail, sticky | `templates/base.html` |
| `9347573919` | mobile-banner | Bottom mobile, 320x50, dismissible | `templates/base.html` |
| `4478966946` | ppc-game-detail | Game detail, below community tabs | `templates/trophies/game_detail.html` |
| `8944812256` | ppc-franchise-detail | Franchise detail + list | `templates/trophies/franchise_{detail,list}.html` |
| `9209595625` | ppc-company | Company detail + list | `templates/trophies/company_{detail,list}.html` |
| `3933218215` | ppc-engine | Engine detail + list | `templates/trophies/engine_{detail,list}.html` |
| `5392580059` | ppc-genre-theme | Genre/Theme list + Tag detail (shared template) | `templates/trophies/{genre_theme_list,tag_detail}.html` |
| `7312166386` | ppc-flagged-games | Flagged games browse | `templates/trophies/flagged_games.html` |
| `2766416714` | ppc-recently-added | Recently added browse | `templates/trophies/recently_added.html` |
| `5270350613` | ppc-browse-games | Main games browse | `templates/trophies/game_list.html` |
| `7440158895` | ppc-dashboard-inline | Dashboard, between site heartbeat and tab bar | `templates/trophies/dashboard.html` |
| `6127077227` | ppc-community-hub | Community Hub, between feature grid and Discord callout | `templates/community/hub.html` |
| `8195453976` | badge-list | Badge browse | `templates/trophies/badge_list.html` |
| `1939843878` | profile-detail | Profile detail | `templates/trophies/profile_detail.html` |
| `6036194497` | trophy-list | Trophy browse | `templates/trophies/trophy_list.html` |

## Suppression Rules

Ads are gated in exactly one place: the `ads` context processor at `plat_pursuit/context_processors.py`. Templates never check `user.premium_tier` or `request.path` directly; they check `ADSENSE_ENABLED` which reflects the combined gate.

**Suppressed when any of:**
- `settings.ADSENSE_ENABLED` is False (env-level kill switch)
- Path begins with `/accounts/`, `/staff/`, `/api/`, `/admin/`, or `/fundraiser/`
- Authenticated user has `premium_tier` set (any value)

**Not currently suppressed (by design):**
- Dashboard (`/`) — free users see one in-content slot, premium users see nothing via the premium check
- Community Hub (`/community/`) — same behavior as dashboard
- Landing page (`/?no-auth`) — sidebars and mobile banner render; no in-content slot

## Consent (CMP)

**Provider:** Google Funding Choices (free, Google-certified, native Consent Mode v2 support).

**How it loads:**
1. Consent Mode v2 default-deny inline script runs first
2. Funding Choices script (`fundingchoicesmessages.google.com/i/pub-<id>?ers=1`) loads async
3. `signalGooglefcPresent` iframe is injected so AdSense knows a CMP is present and waits for consent before serving personalized ads
4. AdSense loader script (`pagead2.googlesyndication.com/pagead/js/adsbygoogle.js`) loads async

**AdSense dashboard config (not in code):** GDPR message is created under **Privacy & messaging → GDPR**. Region targeting and message styling are managed there. Monitor consent rate in that dashboard — low consent rate indicates message copy or UX issues.

**Privacy policy:** `templates/pages/privacy.html` describes the CMP behavior to end users. Keep that text in sync with what the CMP actually does.

## Content Signals (why gaming-targeted ads appear)

AdSense picks ads based on page content plus user profile. Low-signal pages (icon + list, no prose) get generic filler ads. To push AdSense toward gaming-category advertisers:

- **Meta description**: Detail pages override the `meta_description` block with gaming-rich copy ("Trophy guide, roadmap, and community ratings for {game}"). See [SEO & Meta Tags](../reference/seo-meta-tags.md).
- **Open Graph `og:type=video.other`**: Closest OG Protocol type for games. Reinforces content category.
- **JSON-LD VideoGame schema**: `core/templatetags/seo_tags.py:jsonld_game` produces `schema.org/VideoGame` on game detail pages including name, platform, publisher, genre, release date, time-to-complete. This is the single highest-impact content signal we feed crawlers.
- **robots.txt**: `User-agent: *` permits Mediapartners-Google (AdSense crawler). No separate block needed.

**What we DON'T do (and why):** No gaming-focused ad network (Ezoic, Playwire, Venatus) integration yet. These networks have direct relationships with gaming advertisers and typically deliver 2-5x RPMs versus AdSense for gaming traffic — they are what PSNProfiles and similar sites use. Not applicable at current traffic volume; revisit at ~15k monthly pageviews (Ezoic) or ~500k (Playwire). See the *Future: Gaming-Network Migration* section below.

## Adding a New Ad Slot

1. Create the ad unit in AdSense dashboard (**Ads → By ad unit → Create ad unit → Display**). Use responsive/square for flexibility.
2. Optionally create a Custom Channel with the same name (reporting-only; not required).
3. Copy the 10-digit slot ID from the generated snippet.
4. In the target template, add `{% include 'partials/ad_unit.html' with ad_slot="<slot_id>" %}` at the desired position.
5. Verify the page still suppresses for premium + for any applicable path prefix.
6. Update the **Slot Inventory** table above.

## Adding a New Suppression Path

Only the `ads` context processor is the right place — never add template-level checks.

1. Add the path prefix to `no_ad_prefixes` in `plat_pursuit/context_processors.py:ads`.
2. Confirm every URL that starts with that prefix should actually suppress ads.
3. Update the **Suppression Rules** section of this doc.

## Integration Points

- [SEO & Meta Tags](../reference/seo-meta-tags.md) — meta tags and JSON-LD drive AdSense content categorization
- [Security](../guides/security.md) — CSP allowlist must include AdSense + Funding Choices domains
- [Template Architecture](../reference/template-architecture.md) — context processor registration and base.html structure
- [Subscription Lifecycle](subscription-lifecycle.md) — `premium_tier` field drives the premium ad suppression

## Gotchas and Pitfalls

- **Never deploy with `ADSENSE_TEST_MODE=True`** in production env. It serves Google's test-ad pool which pays $0. If you see only generic/blank ads on a live site, this is the first thing to check.
- **CMP is not optional for EU/UK/EEA traffic.** Disabling or misconfiguring Funding Choices puts the site in violation of GDPR/ePrivacy and AdSense's own policies. Monitor the "Messages shown" and "Consent rate" metrics in AdSense → Privacy & messaging.
- **Funding Choices URL uses `pub-XXXX`** (no `ca-` prefix) — the base.html template uses `{{ ADSENSE_PUB_ID|cut:'ca-' }}` to strip the prefix. Keep this intact when editing.
- **Never check `premium_tier` or `request.path` directly in templates.** The `ads` context processor is the single source of truth; scattering checks causes drift and missed premium suppression.
- **Reusing a slot ID across many pages loses per-page revenue data.** Always create a distinct slot ID per page type. The old `6809027176` was reused across 10+ templates — split in 2026-04 to fix this.
- **Low gaming-ad relevance is almost always a content-signal problem, not a config problem.** If a page has low relevance, check that it overrides `meta_description` + `og_type` with gaming-specific copy. Generic site-default meta tags produce generic ads.
- **Consent Mode v2 default-deny must load BEFORE the AdSense loader.** If the order in base.html is changed, non-personalized ads will serve even to consenting users until the loader gets the updated flags. Keep the order: default-deny → Funding Choices → signalGooglefcPresent → adsbygoogle.js.
- **CSP breaks silently if a new CMP or AdSense domain is introduced.** Google periodically adds new ad-serving subdomains. Browser console errors under `Content-Security-Policy` are the tell. CSP lives in `settings.py` under `CONTENT_SECURITY_POLICY`.
- **The `ad_unit.html` partial self-hides when AdSense reports `data-ad-status='unfilled'`** via MutationObserver. This prevents empty-box layout shift. If you're debugging "ads not showing," check the element's `data-ad-status` attribute in DevTools.

## Future: Gaming-Network Migration (Deferred)

Not implementing now; documented so a future revisit doesn't have to rediscover the landscape.

Gaming-focused ad networks layer on top of or replace AdSense and bring direct-sold demand from game publishers (Epic, Steam, Ubisoft, Square Enix, etc.). They use Prebid.js header bidding to let multiple demand sources compete per impression. Typical RPM uplift for gaming traffic: 2-5x.

| Network | Pageview Threshold | Notes |
|---------|---|---|
| Ezoic | ~10k monthly | Lowest barrier, revenue-share model, can wrap AdSense |
| Venatus / Nitro | ~100-500k monthly | Gaming-first networks |
| Playwire | ~500k monthly | PSNProfiles-tier, premium gaming inventory |
| Raptive | ~100k monthly | Premium editorial sites, less gaming-specific |

**When to revisit:** First decision point at ~15k sustained monthly pageviews (Ezoic). Prerequisite in all cases: CMP + content signals in place (both shipped in the 2026-04 initiative — good to go).

**What migration would look like:** For Ezoic, replace the AdSense loader script with the Ezoic script and Ezoic manages placements. For Playwire/Venatus, they provide Prebid.js config and direct slot control — AdSense stays as one demand source among many. In both cases, the `ad_unit.html` partial and suppression logic stay intact; only the loader and the demand sources change.
