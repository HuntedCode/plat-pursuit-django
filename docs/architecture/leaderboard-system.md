# Leaderboard System

The leaderboard system ranks hunters by badge progress and Badge Points, per series and globally.

> **Migration COMPLETE (2026-08).** There is one backend: Postgres. The Redis sorted-set leaderboards and
> `redis_leaderboard_service` / `leaderboard_service` / `xp_service` were deleted in badge cutover step
> 5b.4. History: [leaderboards-rebuild.md](../design/rebuild/leaderboards-rebuild.md),
> [badge-backend-rebuild.md](../design/rebuild/badge-backend-rebuild.md).

## Architecture Overview

### One backend

| Board | Store | Notes |
|---|---|---|
| Trophies | `Profile`'s own counters | All games, not badge-scoped. Was "Global Progress" -> "Badge Trophies" -> this |
| Badge Points (global + country) | `ProfileBadgeStanding` | Also carries `badges_held`, the secondary stat |
| Badge Points, per edition | `ProfileEditionStanding` | The edition FILTER. Same columns, same names, pre-sliced |
| Per-series board | `SeriesBadgeStanding` | Earners + chasers MERGED into one board |
| Per-series board, per edition | `SeriesEditionStanding` | The edition FILTER on badge detail. One row per STARTED edition, with that edition's own points AND its own `advanced_at` |
| Career XP | `ProfileCareerStanding` | No Redis equivalent ever existed |

All of it is `trophies/services/badge_leaderboards.py` ("Lane B"): indexed reads over denormalized
standing columns, written by the recompute the sync path already runs -- with ONE exception, the
Shovelware Free board, whose store is written by a nightly batch command (see below). No sorted sets, and
identity is read live at render so a renamed hunter cannot show a stale name.

**What the Redis backend cost, and why it is worth remembering.** It needed a rebuild cron
(`update_leaderboards`), incremental writers on four separate paths, a link-time backfill in
`verification_service` because those writers gated on `is_linked`, and a reconciliation pass because the
increments drifted anyway. All of that existed to keep a second copy of numbers Postgres already had.
The standings are recomputed from scratch on every evaluation, so there is nothing to drift and nothing
to reconcile. If a future board feels like it needs a sorted set, re-read this paragraph first.

### The surfaces (steps 4-8, complete)

Boards live on the thing they RANK; the hub is a discovery layer over them. There is exactly one
canonical location per board, so no two pages can drift.

| Surface | URL | What it is |
|---|---|---|
| Global Boards | `/leaderboards/` | Trophies / Badge Points / Career XP, `.pp-switch` tabs, country + edition FILTERS |
| Game board | `/games/<id>/` Ranks panel, `/games/<id>/leaderboard/` | Everyone who has played it |
| Badge board | `/badges/<slug>/` Ranks section (fetched from `/badge-ranks/<slug>/`) | Earners + chasers, merged |
| Job board | `/jobs/<slug>/?tab=ranks` | One job's XP board, paginated |
| Jobs catalogue | `/jobs/`, `/jobs/<slug>/` | In the BROWSE hub; Contracts + Ranks tabs, public |

**Leaderboards is a hub with NO sub-nav rail**, which is the shape it was designed with and returned to.
It briefly carried three board DIRECTORIES (`/leaderboards/{games,badges,jobs}/`) -- paginated catalogues
of boards, sharing one `BoardDirectoryView` base. They were removed in 2026-08, without redirects, having
never left a dev machine.

They were removed because the thin-directory rule that kept them from becoming second copies of `/games/`,
`/badges/` and `/jobs/` had, in the end, left nothing that those pages did not already do. Their one
distinguishing sort already existed on each browse counterpart -- `played_count` on Browse Games, "Most
earned" on Browse Badges, a hunter count on every job card -- and their min-entrants gate only ever HID
entities. Nothing linked to them except the hub rail, which existed because they did.

Country is a FILTER on the full boards only, never a board of its own: one WHERE served by a
`(..., country_code, ...board order)` composite.

### The two filters on Global Boards

Both change what the board READS rather than post-filtering it, which is what keeps a slice the same cost
as the whole thing.

| Filter | Applies to | Mechanism |
|---|---|---|
| Country | all five boards | a WHERE on `country_code`, served by `(country, ...board order)` |
| Edition | **Badge Points only** (of the five here) | a different STORE: `ProfileEditionStanding`, indexed `(edition, [country,] ...board order)` |

Badge detail's Ranks tab carries the same two, and its edition filter works the same way one level down:
a different STORE (`SeriesEditionStanding`), never a WHERE over the series board. See **The per-edition
badge board** below.

Edition exists because Legacy HD and Ultra HD are genuinely different games -- XP accrues per GROUP BADGE,
not per series -- so "who leads Legacy HD" is a question the all-editions board cannot answer. It applies to
Badge Points alone OF THE THREE HERE: an edition is a PlatformGroup, i.e. a badge concept, and neither
Trophies (every game) nor Career XP (the jobs economy) has editions to slice. A control that renders but
changes nothing promises a slice that does not exist.

The rule is "wherever a badge is the subject", not "on this page only" -- badge detail's Ranks tab slices
the same way, off its own store (`SeriesEditionStanding`, below).

### The Trophies board is not badge-scoped, deliberately

It reads `Profile.total_plats` / `total_trophies`, maintained incrementally by the EarnedTrophy signals and
reconciled nightly by `recalc_profile_counters`. **Nothing is denormalized for it.**

It replaced a "Badge Trophies" board that counted trophies across badge-stage games. Keeping that figure
current meant a full-library `EarnedTrophy` aggregate per profile inside `badge_xp.recompute_standing` --
affordable while that seam ran only from `evaluate_badges`, and a per-sync cost the moment the engine was
wired into `sync_complete` (badge cutover 5a). That is the same inline-aggregate pattern `recalc_earn_rates`
exists to undo after the May 2026 incident.

It is also a better board. "Trophies earned in games that happen to have badges" largely measures how many
badge-covered games somebody has played; platinums earned is the figure every hunter already knows about
themselves. The five boards read one per domain: hunting excluding shovelware (the DEFAULT), hunting
weighted by rarity, all hunting, badges, career. The first three group behind ONE chip in the strip --
they answer one question and differ only in what counts.

`ProfileEditionStanding` names its columns identically to `ProfileBadgeStanding`, so `badge_store(edition)`
picks a manager and every board query stays as written. An unrecognised key returns an EMPTY store rather
than falling back to all editions: silently widening would show the global board under an edition heading.
The view validates the key first, so that path only runs on a bug.

**Editions do NOT overlap** for the figures that remain: a group badge belongs to exactly one platform
group, so per-edition XP and badges-held sum to the all-editions totals. (Per-edition TROPHY counts DID
overlap -- a cross-gen game qualifies for both groups -- and went with the Badge Trophies board.)

### The per-edition badge board

Badge detail's Ranks tab slices by edition (`?edition=<platform_group_key>`), and that slice reads
`SeriesEditionStanding` -- one row per (profile, series, STARTED edition). It went through three stores
before landing there, and the wrong turns are worth keeping:

| read | why it was wrong |
|---|---|
| `UserGroupBadge` (earners) | Genuinely per (series x edition), so it looked right. It holds only FINISHERS, so a badge with chasers and no finishers emptied under every edition. A filter must SCOPE a board, not swap it for a rarer one |
| `SeriesBadgeStanding`'s JSON maps | Right population, wrong shape. See below |
| `SeriesEditionStanding` | A table, migration 0313 |

The JSON version ordered on `Cast(group_xp -> key)` and gated membership on
`Cast(group_progress -> key -> 0) > 0`. Two problems, and the second is the one that mattered:

1. **Unindexable.** `sbs_series_board_idx` narrowed the read to one series and then every row was
   extracted, filtered and sorted from the heap. Nothing could stop early, the count and the rank paid it
   too, and the virtualizer re-runs the query per window -- so scrolling a popular badge re-sorted the
   whole series per screenful.
2. **It tiebroke on a date from a different edition.** `SeriesBadgeStanding.advanced_at` is SERIES-wide
   (`compute_series_standings` takes the furthest-along edition's), so two hunters tied on Legacy HD
   points were separated by their Ultra HD progress. **Advancing in one edition could drop a rank in
   another** -- indefensible on a board somebody is chasing. The per-edition date already existed in the
   engine (`_advanced_at` takes one edition's `GroupBadgeResult`) and was being discarded.

**Only STARTED editions get a row.** That is the difference between this store and `group_progress`, which
deliberately keeps untouched editions as `[0, gating]` so the Collection wall has a denominator. A board
has no use for that row, so the membership rule moved to the WRITE side where it costs nothing to apply.

**Two stores that must agree.** `badge_xp.recompute_standing` writes the edition rows in the same pass that
writes `SeriesBadgeStanding`, as a full REPLACE per (profile, series), and deletes them when the series
zeroes out. Both halves are covered by tests, because the failure is silent: an edition board ranking a
hunter the series board has already dropped.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/badge_leaderboards.py` | Every board read: the `*_KEYS` orders, `_ahead_q`, `hydrate()`, `BoardPaginator`/`BoardPage`, `board_count()`, `active_editions()` |
| `trophies/services/badge_xp.py` | The write seam: `recompute_standing` materializes the standings (xp, progress, `advanced_at`, badges held, and the per-edition board rows). Runs on every sync -- it must never grow a profile-wide aggregate |
| `trophies/services/game_leaderboard_service.py` | Per-game boards. Where the rank-equals-position rule was first solved |
| `trophies/views/badge_views.py` | `OverallBadgeLeaderboardsView` (the landing) + `BadgeRanksPanelView` (badge detail's board fragment) |

## Who is ON a board

**Every board is gated on `Profile.is_linked`** -- `badge_leaderboards._linked()`, one rule for all of
them. Only the Trophies board had it before 2026-08, which meant Badge Points ranked the scraped,
unverified profiles the catalogue collects (scout accounts among them): `evaluate_badges --all` walks
every profile with a PSN username, not every linked one, so those standings are real rows. A hunter could
be on one tab of `/leaderboards/` and absent from the next.

The gate is at READ, not at the write seam. Standings for unlinked profiles still exist and are still
what a profile-scoped surface reads; they are simply not competitors. It also means verifying an account
puts a hunter on the boards immediately, with no re-evaluation.

GAME boards are the one exception and do not go through this module: they record who PLAYED a game, which
is catalogue data, and `game_leaderboard_service` owns them with its own `members_only` toggle.

**It is a COLUMN on every store, not a join** (migration 0308), for the same reason `country_code` is:
a predicate that lives on another table cannot go in this table's indexes. The join read `is_linked` out
of the heap of a 48-column `Profile` once per candidate row, on public uncached pages. 0309 then makes the
three whole-table board indexes partial on it -- the shape 0307 measured at `trophy_rank` 16.0 ms -> 3.9
ms on `Profile`.

The per-entity stores (series, edition, earners) were left with plain indexes at that point, reasoning
that a leading key already narrows them to one entity's rows. **0311 made them partial too**, because that
reasoning assumed PAGINATION: under virtual scrolling a reader can be at row 30,000 of a popular series
and the scan fetches `is_linked` per candidate on the way. Every board index in the module is partial on
it now -- `ses_board_idx` and `ses_board_cc_idx` were simply born that way in 0313, which is what a rule
looks like once it has settled: the migration that introduces a board index no longer has to be told.

Two paths keep the mirror honest, and BOTH are needed:

1. Every recompute seam stamps it on the rows it writes (`badge_xp.recompute_standing`,
   `contract_service.recompute_career_standing`, `badge_apply.apply_changes`, `grant_job_xp`).
2. `signals._propagate_profile_flags_to_standings` catches what those miss -- a hunter VERIFYING, which
   moves the value with no recompute behind it. Without it they would stay off every board until their
   next sync, having just done the thing that is meant to put them on.

`profile_mirrored_standings()` is the list both paths share, and a test asserts it against what the models
actually declare, because a store left out of it does not error -- it just quietly ranks the wrong people.

## How a board is served

Every board is one indexed Postgres read. There is no cache, no cron and no sorted set.

1. `badge_store(edition)` / `trophy_store()` pick the standing table for the tab (and the edition slice, if filtered).
2. `_slice()` applies the country filter -- a WHERE served by a composite index, not a post-filter.
3. `BoardPaginator` orders by that board's `*_KEYS` and slices the page.
4. `hydrate()` joins identity (name, avatar, country) at render, so a renamed hunter can never show a
   stale name.
5. `*_rank()` answers "where am I" by COUNTing everyone ahead, expressing the SAME key list via
   `_ahead_q`.

The standings themselves are written by `badge_xp.recompute_standing`, which the sync path already runs.
They are recomputed from scratch each time, so no incremental writer exists to drift.

## Gotchas and Pitfalls

- **A board's rank and a board's row numbering are two different definitions, and they only agree if the
  ordering is TOTAL.** `page()` numbers by slot; `*_rank()` counts everyone ahead. Every board's canonical
  order therefore ends in `profile_id` (the `*_KEYS` tuples) and the rank count expresses that same full
  key list via `_ahead_q`, the way `game_leaderboard_service` already did. Badge Points is quantized to
  `500a + 600b`, so large ties are the norm, not an edge case: without the unique tail every member of a
  tie group was told the group's FIRST slot.

- **A board's membership rule (`> 0`) belongs in the service, beside the rows.** It lived in the view as a
  hand-rolled copy next to the paginator, and the copy drifted -- Career grew the filter in the service and
  not in the view, so the last page ran past the total the footer promised. `board_count()` is now the one
  definition, read by the paginator and the header tally alike.

- **`recompute_standing` must not grow a profile-wide aggregate.** It runs on every sync. It briefly
  carried the badge-game trophy tally (a full-library `EarnedTrophy` scan) and that is exactly what
  `recalc_earn_rates` was created to remove from `sync_complete`. Everything the seam writes should derive
  from the DesiredState it was handed plus bounded per-profile reads.

- **The edition split's `Game` join is gone with the tally.** Recorded because the reasoning still applies
  to anything that reintroduces a per-edition trophy figure: Checked against the
  emitted SQL, not assumed: `game_id IN (SELECT ...)` reads Game under its own alias, so the outer query
  was *not* already joining it. The added join is a PK probe over the rows that survive the IN test, so it
  is strictly cheaper than the `Trophy` join the same query already pays -- a constant factor. It is on the
  SYNC path only. To measure it on real data, time `evaluate_badges --username <whale>` before and after.

- **Rendering a board does no Python aggregation at all.** Every board is an indexed `ORDER BY` plus one
  `hydrate()`. The Python loop in `badge_trophy_tallies` iterates the GROUPED AGGREGATE (one row per
  distinct platform list x tier, bounded by the catalogue's platform vocabulary), never `EarnedTrophy`
  rows. `test_the_aggregate_does_not_grow_with_the_library` is what stops that distinction eroding.

- **Every store with a `country_code` mirror must be in `signals.profile_mirrored_standings()`.** Missing
  one does not error. It leaves that board ranking a relocated hunter under their old flag while the others
  have already moved them, which only a reader who emigrated would ever notice.
  `ProfileEditionStanding` shipped with the column and without the entry;
  `test_every_store_with_a_country_mirror_is_in_the_propagation_list` now asks the models rather than the
  list.

- **The editions do not sum to the all-editions row, and that is not a bug.** A cross-gen game qualifies
  for both platform groups (the engine matches on platform INTERSECTION), so its trophies count in both.
  Splitting them (half each, or first-match-wins) would make an edition's trophy count disagree with the
  badges that edition awards.

- **A per-edition board must tiebreak on a per-edition date.** `SeriesBadgeStanding.advanced_at` is
  series-wide, so the edition board ranked hunters tied on one edition by their progress in another --
  advancing on PS5 could drop a rank on Legacy HD, with nothing on the board the reader was looking at
  having changed. `SeriesEditionStanding` stores each edition's own date (migration 0313). The general
  rule: a board that SCOPES a population must scope every key it orders on, not just the leading one.

- **The scoped-recompute invariant is what makes the per-edition store safe to prune.**
  `recompute_standing` deletes every edition row for the series it evaluated and re-inserts from the batch
  it was handed, which is only correct because `group_badges` is guaranteed to contain EVERY live edition
  of any series it touches. (Inline in that function, not a helper. Do not go looking for
  `_write_edition_standings` next to it -- that one writes `ProfileEditionStanding`, a different store.)
  Scoping a recompute by BADGE rather than by SERIES would silently delete the other edition's row --
  `test_badge_sync_wiring` pins the invariant for `group_progress` and it now protects a second store.

- **`recompute_standing` may be scoped to a subset of series, so every profile-wide figure must be
  re-summed from ALL the profile's `SeriesBadgeStanding` rows** -- never from the call's own results. That
  is why per-edition XP is stored per series in `group_xp` rather than only aggregated. Summing the call's
  results instead would silently halve a hunter's edition standing every time one series was re-run.

- **A board's membership rule belongs in the row function, not only on the count that pages it.** An
  edition standing survives on zero points when the hunter has trophies but no cleared gating stage there,
  so an unfiltered read would hand the last page rows the count never promised.

- **A per-entity board needs no denormalized count.** `BadgeSeries.entrants` and `Job.entrants` existed
  only so the directories could GATE and SORT across the whole catalogue before pagination, which is the
  one thing you cannot do on a value computed per row. A single board's count is one scoped, indexed read
  (`series_board_count`, `job_board_counts([slug])`), so both columns went with the directories.
  `Game.played_count` is unrelated and stays -- it predates all of this and feeds the game detail hero,
  the ratings panel, browse cards and the recap.

- **A new badge series needs no rebuild.** This used to be a real chore: the sorted sets only caught
  incremental events, so a newly authored series showed nothing until a rebuild ran. Standings are
  recomputed from scratch on every evaluation, so `evaluate_badges --series <slug>` (or just the nightly
  `--all`) is the whole procedure.

## Management Commands

**Two, and both arrived in 2026-09.** `recompute_clean_standings` writes the Shovelware Free board's
store; `recompute_rarity_standings` writes Rarity Score's. Everything below was written when the answer
was "none", and it is still the right default -- but it now has two exceptions, and the rule that
distinguishes them is worth stating: a store needs its own writer when its figures are NOT a projection
of something an existing seam already computes. Badge standings fall out of badge evaluation; these two
do not fall out of anything, because a shovelware flag and a trophy's rarity are properties of the
CATALOGUE, not of the hunter, so nothing in the sync path has cause to recompute them per profile.

The original rule, which still holds for anything that IS such a projection:

That includes new stores: `SeriesEditionStanding` (migration 0313) was created empty and populated by the
`evaluate_badges --all` that a deploy runs anyway, exactly as `ProfileEditionStanding` was in 0300. A
bespoke seeder for it was written and deleted -- the only `advanced_at` it could derive was the
series-wide one this store exists to stop using, so it would have produced a board that looks migrated and
still tiebreaks wrong. **If a new standing store ever seems to need a backfill command, check first
whether a full evaluation is already scheduled; it usually is, and it writes better data than a seeder
reading the old shape can.**

`evaluate_badges --all` (nightly) is what keeps the standings honest, but it is a badge-evaluation
command, not a leaderboard one -- see [badge-system.md](badge-system.md).

> **Removed 2026-08:** `update_leaderboards`. It rebuilt the Redis sorted sets from
> `ProfileGamification`. **Its Render cron entry must be deleted by hand at deploy** -- the schedule
> outlives the code and will keep firing against a command that no longer exists. Tracked in
> [prod-deploy-checklist.md](../design/rebuild/prod-deploy-checklist.md).

## Related Docs

- [Badge System](badge-system.md): Parent system; leaderboards rank badge progress and XP
- [Gamification](gamification.md): ProfileGamification model that powers the XP leaderboard
- [Redis Keys](../reference/redis-keys.md): Complete key map for raw Redis and Django cache
- [Cron Jobs](../guides/cron-jobs.md): the nightly `evaluate_badges --all` that keeps standings fresh

## The Shovelware Free board (2026-09)

The fourth Global Board, and the one a bare `/leaderboards/` lands on. It ranks the same hunters as the
Trophies board by the same rule -- platinums, total trophies breaking the tie -- over a narrower
population: trophies earned on games the shovelware detector has not flagged.

It is a **separate board rather than a filter on Trophies**, and that is forced rather than chosen. The
Trophies board ranks `Profile.total_plats` / `total_trophies`, which are denormalized INTEGER COLUMNS.
Country and edition are cheap because they filter the store; shovelware is not a property of the profile,
so there is nothing to filter -- the number itself has to be different, which means materializing it.

| Piece | Where |
|---|---|
| Store | `ProfileTrophyStanding` (migration `0332`) -- one row per linked hunter, `country_code` + `is_linked` mirrored, two partial indexes |
| Rule | `SHOVELWARE_FLAGGED_STATUSES` in `models.py`; `manually_cleared` counts as CLEAN |
| Reads | `clean_store()` / `clean_rows()` / `clean_rank()` + `CLEAN_KEYS` in `badge_leaderboards.py` |
| Writer | `recompute_clean_standings`, `nightly` step 2 -- the ONLY writer |

Three things that are easy to get wrong:

- **It has no incremental path, deliberately.** Shovelware is a property of the GAME, so re-flagging one
  invalidates every hunter who earned a trophy on it -- a fan-out no per-row signal can see. The
  equivalent aggregate inside a sync seam is exactly what got `ProfileBadgeStanding`'s trophy counts
  deleted in 2026-08. It must stay a batch job.
- **Its store is written for LINKED hunters only**, unlike the badge and career stores. Those are written
  for everybody because their rows also serve profile pages, so gating at read means verifying reveals a
  standing that already existed. This store serves one board, so a row for an unlinked profile would be
  computed, stored and never read. The cost: a newly-verified hunter joins this board on the next nightly
  rather than instantly. That is the one exception to the "verifying puts you on the boards immediately"
  rule stated above.
- **Every board figure is UNFILTERED** (2026-09). `hide_hiddens` is a per-hunter DISPLAY preference --
  "keep these out of MY list" -- not a claim about what was earned, so honouring it on a leaderboard
  ranks two hunters by two different rules and leaves the board unreproducible by anyone but its owner.
  `hide_zeros` could not apply in any case: it drops games with zero earned trophies, which contribute
  nothing to a count of earned ones (it does not move `Profile.total_trophies` either, for the same
  reason). All three trophy boards rank on "every trophy synced to us" -- see `Profile.total_trophies_raw`
  below.
- **`clean_trophies > 0` still does NOT imply `total_trophies_raw > 0`**, because the two are written at
  different TIMES -- the profile counters by signal plus a nightly reconcile, this store nightly only.
  That is why `active_countries()` needs this store as a fourth source; omitting it left a country
  unselectable on the very board its hunters appear on.

## The Rarity Score board (2026-09)

The fifth Global Board, and the third of the three that group behind the **Trophies** chip. It ranks
linked hunters by the sum of `100 / trophy_earn_rate` over their **1,000 rarest base-game trophies**.

Points read as *"how many players you would line up to find one who has this"* -- a 1% trophy is worth
100, a 10% trophy 10. So a hunter's score is that total across their rarest thousand, which is the whole
board in one sentence. PURE RARITY: no trophy-type base, so a 0.5% bronze outscores a 40% platinum,
because the board answers "who has done the hardest things" and a platinum on an easy game is not one.

The scale is 1 to 1,000 per trophy and that range is **PSN's, not ours** -- it reports no rate above 100%
or below 0.1%. A perfect score is therefore exactly 1,000,000.

| Piece | Where |
|---|---|
| Rule | `services/rarity_score.py` -- the formula, the scorable filter, the constants |
| Store | `ProfileRarityStanding` (migration `0335`) -- `rarity_score`, `avg_earn_rate`, `scored_count`, the two mirrors, two partial indexes |
| Reads | `rarity_store()` / `rarity_rows()` / `rarity_rank()` + `RARITY_KEYS` in `badge_leaderboards.py` |
| Writer | `recompute_rarity_standings`, `nightly` step 3 -- the ONLY writer |

**Membership is a FULL 1,000 scorable trophies**, not "more than none". Below the cap a hunter's sum is
short *by construction*, so they would rank low for having played LESS rather than for having played
easier -- the one thing this board is not meant to measure. That literal appears in four query sites and
in both partial index conditions, and `rarity_score.TOP_N` is asserted equal to it by test.

### Five things that are easy to get wrong

- **The two rate fields carry different units.** `Trophy.trophy_earn_rate` is PSN's global figure and is a
  PERCENTAGE (`12.3` means 12.3%, rendered as `{{ x }}%`). `Trophy.earn_rate` is ours and is a FRACTION
  (rendered with `|multiply:100`). Feeding the wrong one in scales every score by 100 and nothing errors.
  This board uses PSN's: tens of millions of samples against our ~50,000 linked hunters, so ours is noisy
  on obscure games, and PSN's is the number hunters already recognise from the console.
- **A rate of `0.0` means UNKNOWN, never "nobody has it".** The field defaults to 0.0 and
  `psn_api_service` stores 0.0 whenever PSN omits the figure. Read as a rate it is infinitely rare, so
  under `100 / rate` a hunter with a few hundred unsynced trophies tops the board outright -- silently, no
  error, just an impossible number. `rarity_score.scorable_q` excludes it, and that exclusion is
  MANDATORY rather than defensive: the recompute selects by ordering on RATE (indexed) rather than on
  points, and an unknown rate sorts FIRST, ahead of every genuine ultra-rare. Under a points ordering the
  same omission would be harmless. `scorable_earned()` exists so the filter and the ordering cannot be
  written apart.
- **DLC is excluded outright, and cannot be corrected instead.** PSN divides a DLC trophy's earners by
  everyone who owns the BASE GAME rather than the DLC, so DLC rarity is systematically overstated. On a
  top-N board that is decisive rather than marginal: the inflated trophies simply fill the slots. Our own
  data cannot fix it -- `ProfileTrophyGroup` rows exist only where a hunter EARNED something in a group,
  so we can see engagement but never ownership.
- **AGGREGATE, DO NOT SORT.** The obvious implementation orders a hunter's trophies by rarity and slices
  the first thousand -- a 250,000-row sort per whale, with no index able to serve it, and this codebase
  has already dropped one query of that shape on cost (Browse Hunters' `rarest_avg_plat`). The recompute
  groups by RATE and counts, then walks buckets rarest-first taking `min(n, remaining)`. PSN reports rates
  to one decimal, so the result set is bounded by the ~1,000 distinct rate VALUES rather than by library
  size. Note `--chunk-size` therefore means something different here than in its sibling: that command
  emits one row per profile, this one up to a thousand.
- **Ties are arbitrary, and safe ONLY while nothing row-identified is stored.** Points are strictly
  decreasing in rate only ABOVE the 0.1% floor, and with ~1,000 distinct rates over ~1.03M trophies the
  boundary lands inside a tie bucket for essentially every qualifying hunter. So "the rarest 1,000" is not
  a well-defined SET OF ROWS. It does not matter, because every figure this store holds is a function of
  the rate alone -- any choice of tie members yields the same two numbers. That stops being true the
  moment something persists a row identity taken from the slice: a rarest-trophy pointer, a per-trophy
  breakdown, a cached list of the scoring thousand.

### Not to be confused with the other PP Score

`CommunityTrophyDay.pp_score` is a different measure entirely: `total_trophies + 5*platinums +
3*ultra_rares`, computed per DAY across the whole Discord-linked community and posted by the trophy
tracker. This board was briefly called "PP Score" too, in a field of the same name, before the collision
was noticed; it was renamed before shipping. See [community trophy tracker](../features/community-trophy-tracker.md).

## `Profile.total_trophies_raw` (2026-09)

The Trophies board sorted on `total_plats` (unfiltered) and broke ties on `total_trophies` -- which is
**filter-respecting**: `update_profile_trophy_counts` honours the owner's `hide_hiddens` when it writes.
So two hunters level on platinums were separated by a private setting, and a hunter who hid their whole
library fell off the board entirely, `total_trophies == 0` failing the membership rule.

`total_trophies_raw` is the unfiltered grand total -- the sum of the four type counters, which were
already unfiltered. The board's ordering, its tiebreak and its membership rule all moved onto it, and the
two partial indexes moved with them (migration `0333`). An index left on the old column would simply stop
matching, silently, taking `trophy_rank` back to the seq scan 0307 measured at 16 ms.

| | maintained by | reconciled |
|---|---|---|
| `total_trophies` (filtered) | `sync_complete`, settings POST | **nothing** -- a cron cannot recompute it without each profile's settings |
| `total_trophies_raw` | `EarnedTrophy` signals | `recalc_profile_counters`, nightly, from ground truth |

That second row is the other half of the argument: the filtered figure is the one with no safety net, so
a missed write there persists until that hunter next syncs. It is still what a PROFILE shows, which is
right -- a personal view should honour a personal preference. A shared scale should not.

The backfill is exact and lives in the migration rather than in a deploy step: every earned trophy is one
of the four types, so the new value is their sum and the whole fill is one arithmetic UPDATE over columns
that already exist. There is no window where the board reads zeros.
