# Share Images System

Downloadable PNG cards a hunter posts to show off what they've finished. Two card families exist:
**plat cards** (a completed game) and the **monthly recap** card. Both render HTML through headless
Chromium; nothing is stored.

> **Rewritten 2026-08.** The plat card half of this system was rebuilt end to end: what earns a card,
> how it's keyed, what it looks like, and the page it comes from. Anything you remember about platinum
> `EarnedTrophy` keying, portrait format, a four-card wayfinder, or a 105-gradient picker is gone. The
> recap card is untouched and still uses the older conventions.

## What earns a plat card

**A game's DEFAULT trophy group at 100%.** Not "has a platinum".

That distinction is the whole point of the rebuild. The old rule anchored a card to a platinum
`EarnedTrophy` row, so a game with no platinum could never produce one — excluding every
100%-with-no-platinum completion, which is a real achievement with no way to share it. The default-group
rule is a strict superset (a platinum lives in the default group, so a platinum implies the group is
done), which is why one query yields both card variants:

| Variant | When | On the card |
|---|---|---|
| `platinum` | the game defines a platinum | the platinum trophy icon, "PLATINUM", `#N` from the platinum ladder |
| `full` | the game defines none | a 100% mark, "100% COMPLETE", `#N` from its own separate ladder |

Each variant counts its **own** ordinal ladder. A single shared ladder would have renumbered every
platinum card already shared.

Eligibility is read from `ProfileTrophyGroup` — a per-sync denorm whose `progress` is floored, so it
reads 100 iff every trophy in the group is earned. The new badge system uses the same read for its BASE
bar (`badge_orchestrator.evaluate_with_catalog`), so this is a lookup, never a live aggregate over
`EarnedTrophy`, which is what keeps it whale-safe.

**DLC**: the card is scoped to the default group, which is what makes it safe to hand a platinum to
someone whose DLC is outstanding. Whole-game figures (an "ALL DLC" mark and the full trophy total)
appear only when the whole game is done. A partial figure is never shown beside a PLATINUM label.

## Architecture

Three layers: data assembly, HTML generation, PNG rendering.

**Data assembly** — `core/services/completion_card_service.py`. Decides eligibility, resolves the
variant and ordinal, and builds the payload: game and cover, trophy tier breakdown, playtime, the
hunter's own rating, the badge series + medallion, and the Job Board contract. Reads the NEW
grouping-badge system (`BadgeSeries` / `SeriesBadgeStanding` / `UserTitle` with
`source_type='badge_series'`); the legacy `Badge` / `UserBadgeProgress` tier-1 reads are gone, as is the
legacy badge-XP number.

**HTML generation** — `render_to_string` on `templates/shareables/plat_card.html`, fully inline styles
with hex colours. The `--pp-*` token *values* are ported by hand into that template, with a map in its
header comment; there is no stylesheet at render time (see Gotchas).

**PNG rendering** — `core/services/playwright_renderer.py`. Inlines fonts and images as base64, injects
the theme background, screenshots `.share-image-content`. Runs Playwright in a dedicated daemon thread
(`ThreadPoolExecutor(max_workers=1)`) to keep its asyncio loop away from Django's sync ORM.

**Image caching** — `ShareImageCache` downloads external images to `share_temp_images/` with
deterministic MD5 filenames, so the cache is shared across gunicorn workers with no shared state.

## The Plat Cards page (`/shareables/`)

One page, one job: browse your completions and make a card from any of them.

`PlatCardsView` (`HtmxListMixin` + `ListView`) — paginated, server-side filter and sort, infinite
scroll. Built on the Browse Games / Franchises pattern: accented header + count-up career stats, a
segmented variant filter (All / Platinum / 100%), a quiet toolbar (search, sort, shovelware), the
`.pcard` grid, and a sticky mini-bar.

- The variant toggle is **radios styled as `.pp-switch`**, a filter rather than a view island, so
  switching preserves the active search and sort and browser Back stays correct with no JS.
- **Shovelware is hidden by default here**, unlike Browse Games: these are the hunter's own
  completions, and asset-flip platinums are what they least want to scroll past.
- Header stats read the unfiltered ladders, so they describe the hunter rather than the toolbar.

### What used to be here

| Was | Now |
|---|---|
| `/shareables/` — a 4-card wayfinder landing | The Plat Cards page itself. Three of the four destinations no longer exist |
| `/shareables/platinums/` — browse all platinums, every row in one response | Redirects to `/shareables/`. Keeps its URL name: platinum notifications deep-link it with `?et=` |
| `/shareables/platinum-grid/` — multi-plat collage wizard | Retired. 302 to `/shareables/`; view parked unrouted |
| `/shareables/profile-card/` — profile card builder | Retired and later DELETED outright (view, service, renderer, templates, `share-image.js`). Its successor is the Profile Card on the profile page's Card tab (below). The URL still 302s to `/shareables/` |
| Monthly Recap (surfaced as a wayfinder card) | Unchanged at `/recap/`, with its own subnav entry |

The Game Detail hero also had a "Share Card" button. It was removed so a plat card comes from exactly
one place — which also dropped `share-image.js`, `shareable-manager.js`, `color-grid-modal.js` and an
inlined theme blob off the SEO-inbound page.

## The Profile Card (the profile page's Card tab)

The family's third sibling (2026-08): one 1200x630 landscape card whose subject is the **hunter's
whole career**, deliberately DENSE (his call: "if there is any card that should be dense it's this
one"). Identity strip carries the worn title and the mark's full name (supporter tier / Staff /
Moderator) in its colour. The trophy side: platinum count + 100% games, per-tier tallies, trophy
level / average completion / games / rarest-platinum %, a flavor line naming the rarest and latest
platinums, then a fixed-shape career row (jobs played of catalog, tiers earned, career XP,
collection %). The Pursuer side: disciplines ring around the level, rank, and the full five-
discipline legend with per-family level totals. The spine: newest medallions, held-of-catalog with
open-chase and holo counts, the closest-badge chase with its real progress bar, and the dominant
discipline. It replaces the deleted 2025 profile card and is built entirely on the new systems.

**Where it lives:** its own **Card tab on the profile page**, owner-only -- not on My Shareables
(that page is plat-cards-only; every card has its own singular place). The tab's preview is the
REAL card template rendered server-side with live data and transform-scaled to fit, so preview and
download cannot drift. The view normalizes `?tab=card` away for visitors on both render paths.

**Endpoint:** `GET /api/v1/shareables/profile/png/` (`ProfileCardPNGView`). No key in the URL: the
card is built from `request.user.profile`, so ownership is structural. Always renders on the
`ppSubstrate` ground (the family radial, so the renderer's theme pass is a visual no-op); no theme
picker in v1.

**Data:** `core/services/profile_card_service.get_card_data(profile)` -- every figure is a Profile
denorm (`trophy_snapshot`), a materialized read-model (held `UserGroupBadge` rows), or the
catalog-bounded Career build. The disciplines ring reuses `career_service`'s precomputed
stroke-dash geometry with hexes attached from `completion_card_service.DISCIPLINE_COLOURS`.

**Pins:** `tests/engine/test_profile_card.py` -- the identity literals shared verbatim with
`plat_card.html` (same contract as the recap card's pin), the two embedded faces, no `var(--)`,
ownership on the endpoint and the tab, and graceful degrade when the card build fails.

## The share modal

`templates/shareables/partials/share_modal.html` + `static/js/plat-cards.js`.

The preview is the **real card markup** from the HTML endpoint, rendered at 1200x630 and scaled to fit
with a transform. Preview and download therefore share one template and one theme list and cannot
drift — which they repeatedly did under the old modal.

**Grounds.** Eight designed gradients, as four pairs -- a dark ground and its lifted sibling
(Substrate/Fog, Midnight/Tide, Ember/Clay, Aurora/Retro Wave) -- plus one art ground per
landscape image the game actually has. `Concept.landscape_urls()` is ordered by quality (trusted IGDB
screenshots → artworks → PSN `bg_url`), so a game with several offers each as its own choice, capped at
`ART_OPTION_CAP`. A game with **no** usable art is offered no art ground at all, rather than one that
silently falls back. Art swatches show the actual image; gradient swatches show the actual gradient.

There is no cover-blur ground: a 3:4 cover blown up to 1200x630 is mostly upscale.

**Rating**: the card carries the hunter's own stars, difficulty, grind, fun, their **verdict** and their
quick take, so an unrated game makes a visibly thinner card. The verdict is a coloured pill under the
numbers rather than a fourth cell in them: the others are all "N/10" and it is a phrase, so a cell
would either truncate it or widen every cell to fit "Good game, tough plat". It is also the one
thing on the card that is ADVICE rather than a record, which is what someone reading another hunter's
card is there for. Absent on ratings written before the field existed — the wizard is asking for those,
so a card made today may gain one tomorrow. Two ways in, both driving the same modal through the same controller:

- **The prompt** offers once per opened card on download, and never blocks.
- **The Rate / Edit button** in the modal's action row is permanent, opens PREFILLED from the
  hunter's existing scores, and downloads nothing. The controller is relabelled for it
  ("Save rating", and the skip becomes a plain Cancel), since neither of the prompt's labels is
  true on that path.

Both go through **`PlatPursuit.QuickRate`** (`static/js/quick-rate.js`), the one controller for
**`trophies/partials/game_detail/quick_rate_modal.html`** — the same modal as the Game Detail
Ratings tab, so the two rating surfaces cannot drift. Since 2026-08 the fields inside it are a shared
partial (`partials/_rating_fields.html`) driven by `PlatPursuit.RatingFields`, with `QuickRate` as the
modal wrapper — that is what let the Rate My Games wizard stop carrying its own copy. `QuickRate.open()`'s
contract did not change. This page shipped with the legacy
`rate_before_download_modal.html`, which predates the rebuild: DaisyUI colours and, more importantly, **no
blurb field**, so the card rendered a quick take the only form that could set it never offered. That
partial still exists for `dashboard.html`; it is no longer used here. The guidelines sheet is composed
alongside it because the modal's notice links there, wired by the shared
`PlatPursuit.wireGuidelinesSheet()`.

The controller owns everything except **what happens after a save**: prefill, slider readouts, the
blurb counter, the hours gate, agree-to-guidelines-on-submit, the POST, error surfacing and every
close affordance. Each host passes `onSaved` / `onCancel` / `onDismiss` (and `onOpen` / `onClose` for
page chrome). Game Detail's live panel update and the share modal's preview refresh are the only
page-specific parts left.

**Game Detail links IN**, both ways round now. A finished game shows a **`gd-btn--card`** in the hero's
EXISTING action row (beside My Stats / Add to List / Report), deep-linking to `/shareables/?c=<trophy_group_id>` so the card opens on the surface built
for it. Deliberately a link, not a modal on that page: the share flow is a whole surface (preview,
theme picker, rating controls) and a second copy of it there is the drift the rebuild removed. The CTA
asks `eligible_completions` -- the same predicate the browse page and every endpoint use -- so it can
never offer a card they would refuse, and it is gated on the profile being the VIEWER's own, since the
page also renders another hunter's progress at `/games/<np>/<username>/`. It rides the existing row on
purpose: a finished game then costs the hero no extra height, which a block of its own did.

**The modal's header links out to the game** (`game_url` on the HTML payload). It is built server-side
because `game_detail` keys on `np_communication_id`, not a pk, so JS has nothing to assemble it from --
and it is deliberately absent from the CARD template, since a rendered PNG leaves the site and cannot
carry a link.

Either way, a successful save **invalidates the preview cache** for that completion and refetches.

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/shareables/completion/<trophy_group_id>/html/` | Yes | Preview markup + `art_options`, `variant`, `has_rating` |
| GET | `/api/v1/shareables/completion/<trophy_group_id>/png/?theme=&art=` | Yes | The download |
| GET | `/api/v1/shareables/platinum/<earned_trophy_id>/html/` | Yes | **Legacy alias** — same card, keyed on a platinum EarnedTrophy |
| GET | `/api/v1/shareables/platinum/<earned_trophy_id>/png/` | Yes | **Legacy alias** |
| GET | `/api/v1/recap/<year>/<month>/html/` | Yes | Monthly recap preview |
| GET | `/api/v1/recap/<year>/<month>/png/` | Yes | Monthly recap download |
| GET | `/api/v1/share-temp/<filename>` | No | Serve a cached temp image |

Cards are keyed on the game's **default `TrophyGroup`**, not the `ProfileTrophyGroup` row: TrophyGroup
ids are stable where the denorm may legitimately be rebuilt, and ownership is then answered by the same
predicate that builds the browse list, so a deep link can never render a card the page wouldn't show.

The legacy pair stays because platinum notifications already sent deep-link by EarnedTrophy id, and
because those endpoints carry `TokenAuthentication` — assume external consumers.

`?art=<i>` **indexes the card's own art list** rather than naming a URL, so a request can only select
art the card already offers.

## File Map

| File | Purpose |
|------|---------|
| `core/services/completion_card_service.py` | Eligibility, variant, ordinals, and the card payload |
| `core/services/profile_card_service.py` | The Profile Card payload (whole-career sibling) |
| `core/services/playwright_renderer.py` | PNG rendering: base64 embedding, font faces, theme CSS, thread isolation |
| `core/services/share_image_cache.py` | Fetch + cache external images with deterministic filenames |
| `core/services/share_card_utils.py` | `resolve_temp_path` (all that survives of the old helper set) |
| `core/services/shareable_data_service.py` | `get_rarity_label` only — the notification pipeline shares it |
| `api/shareable_views.py` | `PlatCard{HTML,PNG}View` + `ProfileCardPNGView` + `LegacyPlatinumCard{HTML,PNG}View` |
| `api/recap_views.py` | Recap card endpoints |
| `trophies/views/shareables_views.py` | `PlatCardsView` |
| `templates/shareables/plat_card.html` | **The card.** Landscape 1200x630, both variants |
| `templates/shareables/profile_card.html` | **The Profile Card.** Landscape 1200x630, one variant |
| `templates/trophies/partials/profile_detail/tabs/card_tab.html` + `static/js/profile-card-tab.js` + `static/css/components/profile-card-tab.css` | The profile page's Card tab (inline preview + download) |
| `templates/shareables/plat_cards.html` | The page |
| `templates/shareables/partials/plat_card_results.html` | Grid partial (HTMX swaps + infinite-scroll pages) |
| `templates/shareables/partials/share_modal.html` | The modal |
| `static/js/plat-cards.js` | Page motion, infinite scroll, the modal, themes, deep links |
| `static/css/components/plat-cards.css` | `.pcard` grid + `.pc-modal` |
| `static/fonts/` | The only typefaces a card can use — see `static/fonts/README.md` |
| `trophies/themes.py` | `GRADIENT_THEMES` + the curated `PLAT_CARD_THEME_KEYS` |

## Gotchas and Pitfalls

- **The card renders with no stylesheet.** Playwright uses `page.set_content()` in an `about:blank`
  origin: no CSS file, no custom properties, no network. Every style is inline and every colour is a
  hand-ported hex. Keep the token map in `plat_card.html`'s header in sync with `input.css`.
- **A font not in `static/fonts/` cannot appear on a card**, and a missing one is skipped *silently* —
  the card renders in a Chromium fallback that looks almost right. The renderer reads **STATIC_ROOT**,
  so a deploy that skips `collectstatic` degrades every card invisibly. Guarded by
  `tests/engine/test_share_card_fonts.py`.
- **The preview is injected into the live site; the download is not.** Anything relying on inherited
  CSS lays out differently in the two — unset `line-height` caused exactly this, and the card now pins
  it on every text node. If you add markup, pin its layout.
- **The modal must never scroll, and no ground may hide behind chrome.** `fit()` bounds the preview by
  the room the chrome leaves (viewport 92vh minus header + controls), so extra swatches shrink the CARD
  rather than growing the box. Scaling on width alone was what let each new row push it past 92vh. The
  swatch grid is `auto-fit, minmax(70px, 1fr)` for the same reason: 12 columns at the 1000px box, so
  8 grounds + `ART_OPTION_CAP` still land on one row, and auto-fit collapses the empty tracks so a card
  with no art stretches its 8 instead of leaving a gap. A ninth ground needs the floor lowered in the
  same change -- `test_the_picker_still_fits_on_one_row` says so out loud.
- **The preview cache must be invalidated when a rating is saved.** `previewCache` keys on the
  completion and its comment used to call previews "immutable" — they are not: the card RENDERS the
  hunter's rating. `loadPreview()` was already called after a save and was handed the stale entry
  straight back, so the preview never changed. Anything else that can alter a card must invalidate
  too.
- **The blurb must be prefilled, not just the numbers.** The payload always sends `blurb`, so opening
  the form with an empty textarea and saving CLEARS an existing quick take. That only became possible
  when this page moved to the modal that has the field.
- **An edit form must open prefilled.** The HTML payload carries `user_rating` for exactly this. An
  edit that opens on the form's defaults and saves overwrites real scores with 3/5/5/5 — a control
  that destroys the thing it claims to edit.
- **No minimum preview size.** `fit()` clamps the scale at 0, not at a floor. A floor looks harmless and is not: the box is `overflow: hidden` and the frame carries an inline height, so any floor above the room available makes the preview PAINT OVER the swatch row — measured at 57-82px of overlap on landscape phones, where the picker became unreachable. That is strictly worse than the scrollbar the height budget exists to remove. The card yields to nothing before a ground goes off screen.
- **`fit()` must reset the frame's width before measuring it.** It sets both dimensions from the scale, so reading `clientWidth` without clearing first feeds each run the previous run's answer and ratchets the card smaller on every resize. Same self-referential trap as measuring the box's own height for the budget.
- **Card grounds must not reach the site theme pickers.** They live in the shared `GRADIENT_THEMES` registry so the card can reuse the rendering pipeline, but they are drawn for one 1200x630 layout. Every exporter feeding a site-wide picker filters on `PLAT_CARD_CATEGORY`; only `get_available_themes_for_grid` did until 2026-08, so `get_themes_for_js` (which ships as `window.GRADIENT_THEMES`) was offering them as Monthly Recap backgrounds. The reverse also matters: **don't recategorise `retroWave` to `plat_card` for tidiness** — that filter would delete it from every site picker it has always appeared in. Both directions are tested.
- **A card ground can be lighter, never light.** Every text colour in `plat_card.html` is a hardcoded
  light hex (`#f0f6fd` headings, `#9da5b1` / `#8a939f` sub-text) because the card is inline-styled for
  Playwright and has no tokens. A pale ground puts near-white text on near-white. "Lighter" means
  raising the floor off `#05080c`, not inverting the card.
- **Put a lifted ground's hot spot on the LEFT.** The top-right corner holds the wordmark and the
  `platpursuit.com` link, drawn in the variant accent (`#27ebfe` platinum / `#ff9350` 100%). The dark
  grounds can light that corner (Aurora does) because nothing competes at that value; a lifted one
  washes the link out. Tide was drawn top-right first and had to move.
- **A curated key that doesn't resolve fails SILENTLY.** `get_plat_card_themes` skips a key missing
  from `GRADIENT_THEMES` rather than raising, so a typo just removes the ground from the picker with no
  error anywhere. Pinned by `test_every_curated_key_resolves_to_a_real_theme`.
- **`data-element="platinum-banner"` must stay OFF the plat card.** That hook lets a theme tint the old
  card's identity strip; both consumers would wreck this one (the renderer injects `background` with
  `!important`, and the preview JS paints the legacy theme's cyan tint onto it).
- **The scrim is an INNER layer.** The renderer replaces `.share-image-content`'s background with
  `!important`, so a scrim baked into the ground is blown away and text lands on raw art.
- **Art is a theme, not a fixture.** Painting the backdrop unconditionally puts it on top of whatever
  ground the theme set, making all the options render identically. It happened once.
- **`ShareImageCache` rejects non-`http(s)` URLs.** Medallion layers include `static(...)` paths, so
  routing them through the cache drops them silently, in every environment. Local paths go straight to
  the renderer, which resolves **both** `/static/` and `/media/` into data URIs.
- **A local path that isn't inlined renders as nothing, and only in the download.** `set_content()`
  runs in an `about:blank` origin, so a root-relative `src` has no base to resolve against — no
  exception, no console error we see. The *preview* is a real page on the site origin, so the same card
  looks complete there. `/media/` went unhandled for exactly this reason: a badge's backdrop plate (a
  `static(...)` fallback) rendered while its custom subject art (a FileField `.url`) vanished from
  every downloaded card. Guarded by `tests/engine/test_render_local_images.py`.
- **`/media/` is capped at `MEDIA_MAX_PX` (256); `/static/` is never resized.** Static is our own
  right-sized art; media is whatever a contributor uploaded (badge art is commonly 850x850, and the
  card's medallion is 52px). Uncapped it added up to ~647 KB of base64 per layer. Alpha survives the
  resize — the resizer only drops to JPEG for images that have none.
- **Both local passes are anchored against matching mid-URL.** `https://cdn.example.com/media/x.png`
  *contains* `/media/x.png`; without the lookbehind the substitution splices a data URI into the middle
  of the href and corrupts the `src` rather than merely missing it.
- **Don't `exclude()` on a JSON key.** `exclude(defined_trophies__platinum__gt=0)` does not match rows
  where the key is absent (`->` yields NULL, `NOT NULL` is NULL), so such rows fall out of *both* arms
  of a filter. Cast to integer, as `variant_filter` and every other consumer does.
- **The platinum ordinal is a coupled pair.** `_platinum_ordinal` counts `EarnedTrophy` rows with a
  tuple comparison (`earned_date_time DESC NULLS LAST, -id`); NULL-date platinums sort to the END of the
  timeline and take the LOWEST ordinals. The identity line's total must count the *same* population, or
  a card can read "PLATINUM #47 … 35 platinums".
- **`_full_ordinal` excludes games that define a platinum**, so the platinum-with-no-earned-row
  downgrade is not in its own ladder and must be counted with `self_in_ladder=False`.
- **Every render embeds all registered fonts**, used or not — ~2.5 MB of base64 per document. Cached
  per process, but it's why the image budget is tight (`image_max_size`).
- **`_resolve_urls` compresses share-temp images** to `image_max_size` (default 200). The plat card
  overrides to 1000 for its cover; too small a cap downsamples then upscales and looks soft.
- **Deterministic cache filenames** mean a URL whose content changes (an updated avatar) serves the
  stale file until cleanup.
- **The recap card still uses the older conventions** — Poppins, the `[data-element]` banner hook, the
  full theme registry. It was not part of this rebuild and will look older next to a plat card until it
  gets its own pass.

## Related Docs

- [Badge System](../architecture/badge-system.md) — the medallion + series title the card shows
- [Job Board Contracts](../design/rebuild/job-board-contracts.md) — the contract + jobs the card shows
- [Rebuild Playbook](../design/rebuild/rebuild-playbook.md) — page status
