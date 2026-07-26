# Game-Detail Ratings Tab

The **Ratings** tab on a game's detail page (`/games/<np>/`, `?view=ratings`) is where the community's take on a game lives: an aggregate difficulty/grindiness/fun/hours/overall verdict, per trophy group (base + each DLC), plus optional short written "quick takes." It is the read/write home for `UserConceptRating` (the structured rating model kept after the review hub was archived, see [Review Hub](review-hub.md)).

## What's shipped

Rendered by `ratings_panel.html` → per-group `_rating_conditions.html`, as a stack of full-width framed sections (About-panel `.gd-acard` style) plus one bare card grid:

| Section | What it is |
|---|---|
| **Community snapshot** (framed) | Four per-game numbers denormalized on `Game` (players / platinums / 100% club / avg completion) — zero queries, whale-safe. Recomputed nightly. |
| **DLC selector** | Adaptive: a pill row for a few groups, a Base pill + dropdown once there are many. |
| **The rating** (framed "conditions card") | Apple-Weather model: a synthesized plain-language **summary sentence** headline (`rating_summary`), the aggregate score as a glowing **Tally** (`.pp-tally`), and one **quality tile** per axis (difficulty/grindiness/fun) where the number is the figure and the `rating_verdict` word is its tone-colored (`rating_tone`) descriptor. No chart, by design (polarity lives in the word, so nothing reads backwards the way a bar length can). |
| **Your take** (framed) | When the viewer has rated AND >1 rating exists: a one-line comparison of their scores vs the community (`rating_comparison`). |
| **Quick takes** (bare card grid) | Optional ≤140-char public micro-reviews attached to a rating — the individual human voices. A bounded preview (newest 6 per group), each a distinct card. |

All four rating filters (`rating_tone` / `rating_verdict` / `rating_summary` / `rating_comparison`) are mirrored verbatim by `game-detail.js` for the no-reload live-update after a submit — keep the Python + JS thresholds/wording in sync.

**Quick-take blurbs** (Phase 1, shipped): optional field on `UserConceptRating`, written via the quick-rate modal, sanitized + banned-word filtered on submit, reactively moderated (publish → report → staff soft-hide), with a first-class **community-guidelines** agreement (persistent notice + in-context rules sheet, recorded on submit). See the backend detail in the [API reference](../reference/api-endpoints.md#ratings--quick-takes).

## File map

| File | Purpose |
|---|---|
| `templates/trophies/partials/game_detail/ratings_panel.html` | Tab shell: stats band, selector, per-group panels |
| `templates/trophies/partials/game_detail/_rating_conditions.html` | One group's verdict card + quick-takes grid |
| `templates/trophies/partials/game_detail/_blurb_card.html` | One quick-take card |
| `templates/trophies/partials/game_detail/{quick_rate_modal,blurb_report_modal,guidelines_sheet}.html` | Compose / report / guidelines dialogs |
| `core/templatetags/custom_filters.py` | `rating_tone` / `rating_verdict` / `rating_summary` / `rating_comparison` (mirrored in JS) |
| `static/css/components/game-detail.css` | `.gd-rate*`, `.gd-cond*`, `.gd-blurb*` |
| `static/js/game-detail.js` | `ratingsTab` IIFE: DLC selector, quick-rate submit + live-update, report, guidelines sheet |
| `api/rating_views.py` | `GroupRatingView` (rate, incl. blurb), `BlurbReportView` |

## Data model

- `UserConceptRating.blurb` (CharField 140) + `blurb_hidden` (bool), partial index `rating_blurb_idx`.
- `UserConceptRating.visible_blurbs()` — the ONLY supported blurb read path (present + not staff-hidden, index-backed).
- `BlurbReport` — FKs the rating, so it follows the rating through `Concept.absorb()` with no absorb branch.

## Gotchas and Pitfalls

- **Bare `.gd-*` block names collide — this stylesheet has bitten us twice.** `.gd-rate` (ratings panel) once collided with a per-trophy earn-rate widget (`display: inline-flex`), shrink-wrapping the whole tab to ~460px on desktop (widget renamed `.gd-trate`). The blurb-report modal's `.gd-report*` collided with the hero "Report an issue" modal's `.gd-report*` — both dialogs are on the page, so the shared `.gd-report__select` `background` shorthand wiped the chevron (blurb modal renamed `.gd-breport*`). Before adding any bare `.gd-*` block class, grep for existing use. See [[feedback_narrow_panel_suspect_class_collision]].
- **The stored blurb is plain, UN-escaped text** (`sanitize_text` un-escapes entities). Render it ONLY in an auto-escaped `{{ }}` HTML text context — never `|safe`, never a JS/attribute/JSON-to-client context. `visible_blurbs()` documents this.
- **Per-group queries must stay bounded.** The blurb preview is `visible_blurbs().filter(...)[:6]` with `select_related('profile')`; keep it index-backed and capped (whale-safe).
- **SSR↔JS parity.** The four rating filters have JS twins in `game-detail.js`; a threshold/wording change must move both.

## Deferred / future work — the "blurbs at scale" cluster

These all light up together only when a game routinely has **~10+ quick takes**. Until then they are premature: on a 0–3-take preview they add machinery (and dead-looking UI) with no payoff, and most need a *full* blurb-browsing surface we have not built (there is no "see all" / pagination past the 6-card preview). **Build them as one package when volume justifies it, not piecemeal.**

- **Sort / filter on quick takes** — considered 2026-07 and **parked**. Sorting/filtering a handful of preview cards is dead UI, and it presupposes a full list (with load-more + different indexes, e.g. a star-sort) that doesn't exist. Decisions if/when built: keep sorts to **Recent / Highest-rated / Most-helpful**; **drop "Least Stars"** (nobody sorts reviews worst-first); the **star-rating filter is overkill** (usually 0–1 results) — skip it. Home for these controls is a future "See all N quick takes" view (modal/expanded), not a toolbar over the preview.
- **Helpful votes / consensus** — parked for the same volume reason. Would need a `BlurbVote` model + endpoint + a denormalized count/index (a `main`-branch backend chunk). "Best takes rise" only matters with many takes.
- **"How it compares"** (cross-game percentile: "Harder than 78% of platinums") — the heaviest layer; needs a site-wide rating distribution materialized **nightly** (a cron + aggregate), off the request path. Differentiated and does NOT depend on blurb volume, so it can be built independently; the framed tab structure has a slot for it as one more band.

Cheap interim option (not sort/filter UI): make the 6-card preview show the most *representative* takes rather than strictly newest — a curation tweak on the read query.

## Related docs

- [API Endpoints — Ratings & Quick Takes](../reference/api-endpoints.md#ratings--quick-takes)
- [Review Hub](review-hub.md) — the archived reviews surface; `UserConceptRating` originated there
- [Comment System (Legacy)](comment-system.md) — the shared moderation infra (`BannedWord`, `sanitize_text`, report pattern) the blurbs reuse
