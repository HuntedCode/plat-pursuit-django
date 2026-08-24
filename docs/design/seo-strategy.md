# SEO Strategy

> Decided 2026-08-23 with Jeffrey. This is the strategy of record: the five structural calls, the
> lane plan, and the measurement loop. The full technical audit that fed it lives in the findings
> sections below. Companion reference: [seo-meta-tags.md](../reference/seo-meta-tags.md) (being
> brought current in Lane 1).

## What we are trying to win

| Query family | Our angle | Priority |
|---|---|---|
| `{game} platinum worth it / difficulty / how long` | Ratings from FINISHERS only + the verdict system. We answer the intent question, not the walkthrough question (PowerPyx owns walkthroughs). | HIGH |
| `{game} trophies / trophy list / rarity` | Deep server-rendered trophy pages with real community data. | HIGH |
| `{psn name}` | Public hunter profiles. The sleeper: it is how PSNProfiles earns much of its traffic. | HIGH (with quality floor) |
| Category heads (`trophy tracker`, `PSN profile viewer`) | The landing + hub pages. Clean coverage, not obsession. | MED |
| Badge/series discovery | Unique content nobody else has, but low search demand. Treated as on-site discovery surfaces; SEO effort minimal. | LOW |

**NOT in the strategy:** Roadmaps. Hidden from the site with no return promised (his call, 2026-08-23).
They keep their routes for deep links, but nothing advertises them: no sitemap, no pillar plan. If
they ever return, revisit -- they carry the site's best long-form markup.

## The five structural decisions (2026-08-23)

1. **The indexable game unit is the CONCEPT.** One canonical Game row per Concept (the "best" SKU);
   regional/platform siblings point their `rel=canonical` at it and leave the sitemap. Scale today:
   ~35,000 Game rows over ~18-20k Concepts, so this roughly halves the indexed set before quality
   filters. (Lane 1 defines "best SKU": prefer the row with trophy data + IGDB match + current-gen.)
2. **Profiles are an SEO asset WITH a quality floor.** Indexed: synced, public-history profiles with
   real data. `noindex` + out of the sitemap: never-synced stubs, zero-trophy rows, and
   `psn_history_public=False` profiles (which render header-only pages).
3. **Browse hubs: clean-URL canonicals; filtered/paginated states are `noindex,follow`.** Robots
   never blocks params on browse hubs (it kills link discovery); the canonical consolidates and the
   noindex keeps the junk out. The games hub must return 200 on bare `/games/` (Lane 1 removes the
   force-redirect to `?platform=...`).
4. **Roadmaps: struck.** See above.
5. **Crawl budget: CURATE, don't disarm.** The crawler defenses (bot 301s, anon gate, CF guard)
   exist because of real OOM incidents and stay. Organic growth comes from shrinking the indexable
   set to the affordable, high-value subset: concept-canonical games minus shovelware, floored
   profiles, hubs, and the landing.

## Lanes

- **Lane 0 -- triage (DONE on `rebuild`; ships WITH the cutover -- his call 2026-08-23, no early
  cherry-pick; prod keeps its broken robots until then):** the wrong-today list. robots.txt wildcard rules that blocked every canonical
  game/badge/jobs detail page; the bot-canonical 301 that swallowed `/games/<np>/leaderboard/`
  (and roadmap) sub-pages; querystring-dirty canonicals site-wide; the badge sitemap reading the
  retired Badge model; the roadmap sitemap advertising hidden pages; shovelware + no-floor profiles
  in sitemaps; private-profile thin pages indexed with descriptions of data they refuse to show;
  the broken SearchAction; 404 lacking noindex; fragment endpoints unprotected.
- **Lane 1 -- technical hygiene at scale:** concept-canonical implementation (the Game-row
  canonical election + sitemap swap), titles/descriptions with page/filter awareness, the
  `seo_description` vs hardcoded-block unification (one system), games hub 200, casing redirect for
  profile URLs, seo-meta-tags.md rewrite.
- **Lane 2 -- structured data + social:** AggregateRating on game detail (we HAVE the data),
  ItemList on hubs, VideoGameSeries on franchises, sameAs on Organization, bespoke OG images
  (landing first, badge detail second -- the medallion art deserves better than a 128px logo).
- **Lane 3 -- content depth + CWV:** thin-page rules beyond Lane 0's floors, image dimensions
  (385 imgs, 0 sized), font self-hosting evaluation, a Lighthouse baseline in docs.

## Lighthouse baseline (2026-08-23, Lane 3)

Method: Lighthouse 12.8 CLI, headless Chrome, mobile emulation, against the local dev stack
(Docker `web` on :8000). Dev numbers are pessimistic on transfer (unminified JS, no Cloudflare,
no compression) and the throttled FCP/LCP seconds are lab-relative, NOT what prod users see.
The value is in the structure: CLS, the audit findings, and movement between runs. Re-baseline
against prod after the cutover.

| Page | Perf | SEO | A11y | BP | CLS | Notes |
|---|---|---|---|---|---|---|
| `/` (anon landing) | 59 | 92 | 96 | 100 | 0 | seo ding = the hidden search anchor (fixed) |
| `/games/` | 37 -> 57 | 92 | 95 | 96 | **0.465 -> 0** | drawer collapse after paint (fixed, see below) |
| game detail | 58 | 100 | 96 | 96 | 0 | |
| profile | 58 | 100 | 97 | 82 | 0.001 | BP ding = dev-only http; legacy `http://` PSN avatar URLs noted |

What the baseline drove (all shipped in Lane 3):

- **The 0.465 CLS on `/games/`**: the advanced filter drawer renders open for no-JS and
  `filterPanel` collapsed it after parse, shoving the whole grid up. Fixed with a pre-paint
  inline collapse (`templates/partials/browse/drawer_precollapse.html`) driven by SERVER truth
  (the view's filter_chips / has_advanced_filters signal -- a querystring re-derivation misread
  parked range params, the lane audit's HIGH); included on games hub, company list, recently
  added, and tag detail. Badge gallery deliberately skipped (its open-on-load rule reads JS-side state,
  not the querystring).
- **Fonts self-hosted**: Google Fonts CDN dropped (cache partitioning killed the cross-site
  benefit; it cost two extra origins on every first visit). Variable woff2 subsets in
  `static/fonts/`, faces in `input.css`, latin preloaded from `base.html`, CSP font-src trimmed
  to `'self'`. Poppins stays declared but dormant outside Stellar Circuit (unicode-range +
  nothing else uses it).
- **Image weight**: the four 1000px badge backdrop PNGs quantized in place (1.47 MB -> 172 KB;
  they render at <=400px so the dither is invisible -- eyeball after deploy); the two showcase
  card PNGs get webp siblings behind `<picture>` (943 KB -> 91 KB on-page; PNG stays as the
  og:image target).
- **CLS/LCP attributes**: width/height on the showcase cards (landing + link-psn),
  `fetchpriority="high"` on the two LCP candidates (game-detail cover, profile-header avatar).
- **Thin-page rules beyond the Lane 0 floors**: day pages are `noindex, follow` (profiles x
  dates is an unbounded space of thin slices; empty days already 404). One-game company pages
  are `noindex, follow` (a shovelware publisher's page answers nothing its game page doesn't).

Not chased: `modern-image-formats` on `/media/badges/main/*.png` (badge art is DB/media content
-- a badge-cutover follow-up, not an SEO lane), `unminified-javascript` (dev-server artifact),
the `robots-txt` audit failure on one run (lab flake; robots serves 200 and Lane 0 pins it).

## Measurement

Search Console was never set up. **Jeffrey's action: create a Domain property for platpursuit.com
(DNS TXT verification), submit /sitemap.xml, and grant it a look monthly.** Every lane's success is
checked against GSC impressions/coverage a month after shipping; without it we are decorating.
Deploy checklist carries the re-ping items.

## Gotchas and Pitfalls

- Two host sources coexist in seo_tags.py by design: `jsonld_organization`/`jsonld_website` use
  `settings.SITE_URL` (the site entity is prod, whatever host renders it), everything
  request-scoped (canonicals, breadcrumbs, ItemList, VideoGame, ProfilePage) uses
  `request.get_host()`. On prod they agree; on beta they diverge, which is inert because beta
  sends `X-Robots-Tag: noindex` globally. Recorded so the next audit doesn't re-open it.

- robots.txt wildcards: `*` matches ZERO or more characters -- `/games/*/*` matches
  `/games/<np>/`. There is no robots pattern for "exactly two path segments." That is WHY the
  profile-scoped variants are defended by the middleware 301 + anon gate instead (Lane 0).
- The canonical default strips querystrings site-wide. A page whose params are load-bearing for
  identity must override `{% block canonical_url %}` (none currently need to; `?tab=` views of one
  entity correctly canonicalize to the entity).
- Prod (`main`) and `rebuild` diverge on the badge system: the BadgeSeries sitemap is
  rebuild-only; prod keeps the legacy Badge sitemap until cutover.
- The 2026-08 incident architecture (crawl suppression) is deliberate. Do not lift `Crawl-delay`,
  the bot 301s, or the CF guard without re-reading the outage postmortem in seo-meta-tags.md.
