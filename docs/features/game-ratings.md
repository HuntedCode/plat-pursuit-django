# Ratings

`UserConceptRating` surfaces in two places, and they are mirror images of one question. The **game-detail Ratings tab** answers "what does the community think of this game". The **profile Ratings tab** answers "what does this hunter think of games". Both are documented here, and both are built in `rating_service.py`, so the two definitions of "the community average" cannot drift apart.

## Game-detail Ratings tab

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
| `trophies/services/rating_service.py` | `RatingService` (community averages) **+ the profile half**: `profile_rating_summary`, `build_profile_ratings_page`, `PROFILE_RATING_SORTS` |
| `templates/trophies/partials/profile_detail/tabs/ratings_{tab,results}.html` | Profile tab shell (summary + sort) and its swap target |
| `templates/trophies/partials/profile_detail/rating_list_items.html` | One `.pp-rcard`; also the template the scroller appends |
| `static/css/components/profile-hero.css` | `.pp-taste*`, `.pp-rwall`, `.pp-rcard*` |
| `static/css/components/stars.css` | `.pp-stars` — the shared fractional star bar (taste header, rating cards, quick-rate form) |
| `templates/partials/_rating_fields.html` | The shared form fields, in the order both hosts lay out |

## Profile Ratings tab

`/hunters/<user>/?tab=ratings` — the fourth tab on a hunter's profile, and the only one about **taste** rather than totals. Games, Trophies and Badges all answer "how much"; this is the one thing two hunters with identical numbers would answer differently.

Two layers, and the top one is why it is not just a list. A rating read alone has no scale: 6/10 difficulty means one thing from someone whose average is 3 and another from someone whose average is 8.

| Layer | What it is |
|---|---|
| **Taste summary** (`.pp-taste`) | Their average score as fractional stars, the **same synthesized sentence** the game pages use (`rating_summary` reads averages, and averages are averages whether they came from a hundred people rating one game or one person rating a hundred), then four `.scard` figures: games rated / average difficulty + its verdict word / hours signed off on / quick takes written. Rendered only when they have rated something. |
| **The wall** (`.pp-rwall` → `.pp-rcard`) | One **wide** card per rating (1 column on mobile, `auto-fill minmax(420px)` from `md:`): a full-height **landscape art panel** bled to the card's left edge, then the title with the overall score on its own line as stars + figure, the DLC pack when the rating is for one, **all four scored axes in full** (difficulty / grind / fun / hours) as key-figure-verdict cells, the quick take **whole**, and their score against the community's. |

The card was first built cover-forward and portrait, and **reversed**: that shape is `.pp-gcard`'s, and it carries `.pp-gcard`'s emphasis — the game as the subject, the score as an annotation on it. This tab is the other way round. Turning the card on its side is also what let the rating be shown in full: the portrait version had to pick three of the five axes and clamp the take to three lines, both of which are the card fighting its own content. Nothing on the wide card is clamped or height-reserved — grid rows stretch to their tallest member anyway, so reserving lines only pads the short cards without evening out a single row.

The art followed the same logic one step further. A wide card wants a **wide image**, so the panel draws `Concept.landscape_url` (trusted IGDB screenshots → artworks → PSN GAMEHUB art) rather than the 3:4 cover, which sat in this shape as a tall sliver against short content. It reads only the `igdb_*_image_ids` columns off a match the page already selects, so it costs no query and never touches `raw_response` (pinned by test). The **cover is the fallback** — many concepts have no landscape art — and takes `object-position: top` there, where a screenshot takes centre, because the top is where a cover keeps its logo.

Six sorts (`PROFILE_RATING_SORTS`): recently rated / highest / lowest / hardest / longest / A-Z. Grindiness and fun were deliberately **not** given sorts — hours is what grind feels like in a unit people use, and "most fun" and "highest rated" rank nearly the same shelf.

`build_profile_ratings_page` is **three queries flat** whatever the page size: the ratings, the community scores for the concepts on that page, and the games behind those concepts. Nothing scales with account size.

## The recommendation

The one **directive** field. Every score describes what the platinum was *like* (difficulty, grind, hours, fun, overall); none says whether anyone else should do it, which is the question a trophy site exists to answer. The archived `Review` model carried `recommended` (a bool) and archiving reviews took it with it; this restores it.

| Value | Label |
|---|---|
| `worth_it` | Do it |
| `good_game_bad_plat` | Great game, rough platinum |
| `skip` | Skip it |

**Three, and the middle one is why the field exists**: a platinum can be a bad experience attached to a game worth playing, and no yes/no can say that.

A fourth (`bad_game_good_plat`, "only for the trophy" — the shovelware verdict) was built and then **dropped**, because the two fields already say it together: the **stars rate the game** and the **recommendation rates the platinum**, so a shovelware plat is "Do it" at 1.5 stars. Splitting it across the two controls is what makes three enough. Migration `0295` maps any surviving row to `worth_it` — narrowing `choices` does not touch the column, so a retired value would otherwise sit there valid-to-Postgres and render its raw slug through `get_recommendation_display`.

It sits **after the scores, not before them** — every field above describes what the platinum was like, and this says what that adds up to, so it reads as a conclusion rather than a question asked before you have thought about the answer. (An earlier cut led with it on the grounds that it is the fastest field to answer; ordering by "quickest first" was optimising the wrong thing on a form you meet in bulk.)

Rendered as three labelled tiles **across one row**, never a segmented strip: a strip implies these are points on one axis, and the middle option is a statement about the game *and* the platinum separately rather than a midpoint. One row rather than three stacked is also most of what keeps the modal from scrolling (below).

**Permissive model, strict form.** `blank=True, default=''` keeps every pre-existing row valid and makes `recommendation=''` the "needs one" predicate; the requirement lives in `UserConceptRatingForm`, which every server write path goes through. Note the trap: a `blank=True` field added to `Meta.fields` is **not** required by default, so the form sets `required=True` and reassigns `choices` explicitly (Django would otherwise prepend an empty option, rendering a blank fifth radio).

**A partial update is not a partial wipe.** An omitted `recommendation` on an *existing* rating falls back to the stored value — the same protection the blurb has, and for the same reason: "adjust my hours" must not be able to destroy an answer it never mentioned. It is injected *before* validation rather than restored after, because unlike the blurb the field is required and an absent one would never reach a restore step.

**No backfill is possible** — a declared recommendation cannot be inferred from scores. Instead every pre-existing rating is re-served by the wizard once (below), which is also what gives each one an opportunity to gain a quick take.

### The community split

`recommendation_split` is computed inside `RatingService._compute_averages`, so it rides both existing cache entries with no new key and no new invalidation path, and reaches every consumer — including `GroupRatingView`'s JSON response, which is what the live-update reads.

`recommend_pct` is `worth_it` alone — the middle option says the *game* is worth playing and the platinum is not, so folding it in would report the opposite of what those raters meant. The **denominator is answered ratings**, not all of them — counting the pre-field backlog as "would not recommend" would misreport a beloved game as divisive until it clears. The percentage is always printed **with its N**; there is no display floor.

## Data model

- `UserConceptRating.recommendation` (CharField 20, choices, `blank=True`) — see above.
- `UserConceptRating.blurb` (CharField 140) + `blurb_hidden` (bool), partial index `rating_blurb_idx`.
- `UserConceptRating.visible_blurbs()` — the ONLY supported blurb read path (present + not staff-hidden, index-backed).
- `BlurbReport` — FKs the rating, so it follows the rating through `Concept.absorb()` with no absorb branch.

## Gotchas and Pitfalls

- **Bare `.gd-*` block names collide — this stylesheet has bitten us twice.** `.gd-rate` (ratings panel) once collided with a per-trophy earn-rate widget (`display: inline-flex`), shrink-wrapping the whole tab to ~460px on desktop (widget renamed `.gd-trate`). The blurb-report modal's `.gd-report*` collided with the hero "Report an issue" modal's `.gd-report*` — both dialogs are on the page, so the shared `.gd-report__select` `background` shorthand wiped the chevron (blurb modal renamed `.gd-breport*`). Before adding any bare `.gd-*` block class, grep for existing use. See [[feedback_narrow_panel_suspect_class_collision]].
- **The stored blurb is plain, UN-escaped text** (`sanitize_text` un-escapes entities). Render it ONLY in an auto-escaped `{{ }}` HTML text context — never `|safe`, never a JS/attribute/JSON-to-client context. `visible_blurbs()` documents this.
- **Per-group queries must stay bounded.** The blurb preview is `visible_blurbs().filter(...)[:6]` with `select_related('profile')`; keep it index-backed and capped (whale-safe).
- **SSR↔JS parity.** The four rating filters have JS twins in `game-detail.js`; a threshold/wording change must move both.
- **`annotate_community_ratings` cannot score a DLC rating.** The shared browse helper correlates on the concept alone and hard-filters to `concept_trophy_group__isnull=True`, so a DLC rating scored through it is compared against its **base game's** community — a comparison that renders convincingly and means something else. Correlating on the group instead is not the fix either: a base-game rating carries a NULL group and `NULL = NULL` never matches in SQL, so every base row would come back silently unmatched. `_community_scores` groups in the database and pairs up in Python, which sidesteps both. Pinned by test.
- **The rating card's stat strip is laid out by a CONTAINER query, the only one in the codebase.** Its width is the wall's `auto-fill` track minus the art panel, so the same viewport gives it different widths depending on how many columns fit — a viewport breakpoint put four cells in a 300px body and ellipsised every label. `.pp-rcard__body` is `container-type: inline-size` and the strip goes 2×2 → one row at `@container (min-width: 360px)`.
- **"Rated" means COMPLETE, in ONE place.** It used to mean "a row exists", spelled out separately in nine spots across the wizard queue, `ReviewHubService` and the dashboard provider. It now means "a row carrying a recommendation", and every one of them reads `ReviewHubService.COMPLETE` / `complete_ratings()`. Nine copies of a definition is nine chances for the header to report zero waiting while the queue is still serving.
- **A re-served rating MUST arrive prefilled — this is a silent data-loss hazard.** The form's defaults are difficulty 5, grind 5, fun 5, overall 3.0. A re-queued card that loads blank and is then submitted for its recommendation writes those defaults straight over a considered 8/9/2/4.5, with nothing on screen to notice. The queue therefore sends the stored row (`existing`, `existing_blurb`, `rated_at`), not just a flag. Pinned by `test_a_requeued_rating_arrives_with_its_own_scores`.
- **`get_recommendation_display` has no JS twin.** Every other word the ratings JS prints mirrors a Python function (`rating_verdict`, `rating_summary`, `rating_tone`); a choices label has no such function, so the label comes back **from the API response** (`recommendation_label`). Never hardcode the four strings client-side.
- **A rating hangs off a Concept; a cover and a link need a Game.** `display_image_url` is the site's one cover chain and `game_detail_with_profile` is keyed on `np_communication_id`, so the profile wall resolves concept → owned Game in one bulk query. The pick is **ordered** (platinumed, then furthest progress, then id) because a concept can span several platform SKUs and a card that changed which version it linked to between loads would look like a bug in the link.

## Deferred / future work — the "blurbs at scale" cluster

These all light up together only when a game routinely has **~10+ quick takes**. Until then they are premature: on a 0–3-take preview they add machinery (and dead-looking UI) with no payoff, and most need a *full* blurb-browsing surface we have not built (there is no "see all" / pagination past the 6-card preview). **Build them as one package when volume justifies it, not piecemeal.**

- **Sort / filter on quick takes** — considered 2026-07 and **parked**. Sorting/filtering a handful of preview cards is dead UI, and it presupposes a full list (with load-more + different indexes, e.g. a star-sort) that doesn't exist. Decisions if/when built: keep sorts to **Recent / Highest-rated / Most-helpful**; **drop "Least Stars"** (nobody sorts reviews worst-first); the **star-rating filter is overkill** (usually 0–1 results) — skip it. Home for these controls is a future "See all N quick takes" view (modal/expanded), not a toolbar over the preview.
- **Helpful votes / consensus** — parked for the same volume reason. Would need a `BlurbVote` model + endpoint + a denormalized count/index (a `main`-branch backend chunk). "Best takes rise" only matters with many takes.
- **"How it compares"** (cross-game percentile: "Harder than 78% of platinums") — the heaviest layer AND the most dangerous to ship early. It needs a site-wide rating distribution materialized **nightly** (a cron + aggregate), off the request path. It does not depend on *blurb* volume, but it depends heavily on *rating* volume, which is currently thin (games commonly have ~2 ratings). A percentile computed from a noisy 2-rating aggregate, positioned against a distribution of other noisy aggregates, is false precision: it looks authoritative but is statistically meaningless, and reads as the "nicer-fonts PSNProfiles" anti-reference (visual-identity.md) — it would erode trust, not build it. **Do not build until ratings have real adoption** (stable per-game aggregates — a rating-count floor, e.g. >=10-20 — across a broad set of games). Even a coarse-bucket version ("among the tougher plats") needs the same infra and still wobbles on thin data. The framed tab has a slot for it when the data is there.

Cheap interim option (not sort/filter UI): make the 6-card preview show the most *representative* takes rather than strictly newest — a curation tweak on the read query.

## Related docs

- [API Endpoints — Ratings & Quick Takes](../reference/api-endpoints.md#ratings--quick-takes)
- [Review Hub](review-hub.md) — the archived reviews surface; `UserConceptRating` originated there
- [Comment System (Legacy)](comment-system.md) — the shared moderation infra (`BannedWord`, `sanitize_text`, report pattern) the blurbs reuse
