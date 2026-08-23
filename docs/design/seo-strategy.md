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

- **Lane 0 -- triage (DONE on `rebuild`; robots/middleware/canonical fixes cherry-picked to `main`
  for prod):** the wrong-today list. robots.txt wildcard rules that blocked every canonical
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

## Measurement

Search Console was never set up. **Jeffrey's action: create a Domain property for platpursuit.com
(DNS TXT verification), submit /sitemap.xml, and grant it a look monthly.** Every lane's success is
checked against GSC impressions/coverage a month after shipping; without it we are decorating.
Deploy checklist carries the re-ping items.

## Gotchas and Pitfalls

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
