# Prod Deploy Checklist — Gamification Rebuild

> **Purpose.** The rebuild lives on the long-running `rebuild` branch and does not touch production until launch. Along the way, individual changes accumulate **deploy-time obligations** that can't be captured in code alone: data backfills, one-off command runs, cron registrations, manual dashboard config, and prod-bound PR merges. This doc is the single running list so none of it is lost between now and the cutover.
>
> **How to maintain this.** Whenever a change defers work to deploy (a migration that needs a follow-up backfill, a new command that must be run once, a new cron, a manual config step), **add a row here in the same commit**. Check items off as they're done. When the rebuild ships, this doc is the runbook.

## How to read this

| Field | Meaning |
|---|---|
| **When** | `Now` = a prod task outstanding today (already on `main`/prod path) · `Launch` = run at/after the rebuild cutover · `Post` = after launch, once data settles |
| **Idempotent?** | Safe to re-run? (re-runs that double-count or clobber are flagged) |
| **Blocks** | What stays broken / empty until this runs |

---

## Launch tasks (rebuild cutover)

### Data backfills & one-off commands

| # | Task | Command | When | Idempotent? | Blocks | Done |
|---|------|---------|------|-------------|--------|------|
| 1 | **Compute badge rarity** — populates `Badge.earned_count`, `rarity_pct`, `rarity_rank`, `rarity_class` | `python manage.py recalc_badge_rarity` | Launch (after migrations) | Yes (recomputes from scratch) | Frame back-of-card "Earned by N" + "Rarity %/#rank" slots render empty | ☐ |
| 2 | **Backfill earn ranks** — stamps `UserBadge.earn_rank` on historical earners (NULL ranks only), ordered by `earned_at` | `python manage.py backfill_earn_ranks` | Launch (after migrations) | Yes (skips already-stamped rows) | Frame "Earn rank" engraving missing on all pre-existing badges | ☐ |
| 3 | **Backfill per-edition `group_progress`** — populates the new `SeriesBadgeStanding.group_progress` read-model (per-edition `[cleared, gating]`) on every existing standing. `recompute_standing` writes it on each sync, but pre-existing standings hold `{}` until re-evaluated. | `python manage.py evaluate_badges --all` | Launch (after migrations) | Yes (full recompute from scratch) | Collection wall + badge-detail per-edition progress read empty/stale for pre-existing standings until their owner next syncs | ☐ |
| 3a | **Migration `0286_alter_seriesbadgestanding_group_progress`** — help_text only, no data change and no column change. Applies on deploy with the rest; listed so it is not mistaken for a schema change needing a backfill. | (auto, on `migrate`) | With the collection work | Yes | nothing | ☐ |
| 3b | **Re-run after the read-model widened (2026-08)** — `recompute_standing` now stores an entry for every EARNABLE edition, not only ones with cleared > 0, so an untouched edition carries its real `[0, gating]`. Until this runs, the Collection caption still shows no chase count on an edition the hunter has not started (it degrades to blank, never to a wrong number). Same command as #3; idempotent. | `python manage.py evaluate_badges --all` | With the rarity/collection work | Yes (full recompute) | "0 / N stages" missing on unstarted editions | ☐ |
| 4 | **Backfill grouping read-models** — materializes `Genre/Theme/Franchise/Company.representative_game` (the tile + detail-hero cover) AND `Genre/Theme.related_tags` (the detail-page related rail) for the rebuilt `/genres/` list, `/genres\|themes/<slug>/` detail, `/franchises/` list + detail, AND `/companies/` list pages. The franchise cover pick honors `is_excluded`/`is_spinoff` link flags. Until it runs, tiles render the neutral placeholder and tag detail pages show no related rail. Then keep fresh via the daily cron below. | `python manage.py recompute_tag_covers` | Launch (after migrations) | Yes (recomputes from scratch; stable pick) | `/genres/` + `/franchises/` + `/companies/` tiles show the glyph placeholder; tag detail pages omit the related-tags rail | ☐ |

> **Ordering:** #1 and #2 depend on the STEP 2 schema migrations (`UserBadge.status`/`earn_rank`, `Badge` rarity fields); #3 depends on the `SeriesBadgeStanding.group_progress` migration (`0276`); #4 depends on the `Genre/Theme.representative_game` + `.related_tags` migrations (`0277`, `0278`), the `Franchise.representative_game` migration (`0279`), AND the `Company.representative_game` migration (`0280`). All run after migrations; run order between them doesn't matter. **Note:** #3 was verified on beta (evaluate_badges backfills group_progress as expected) — it still needs a run on prod at cutover.

### Cron / scheduling

| # | Task | When | Done |
|---|------|------|------|
| 1 | **Register `recompute_tag_covers` cron** — new daily Render Cron Job (`python manage.py recompute_tag_covers`, 03:45 UTC) keeping the genre/theme tile covers fresh as games sync. Documented in [cron-jobs.md](../../guides/cron-jobs.md). The one-time backfill is task #4 above; this is the ongoing schedule. | Launch (Render dashboard) | ☐ |

### Manual config (dashboards, env, third-party)

| # | Task | When | Done |
|---|------|------|------|
| M1 | **Pause the `send_monthly_recap_emails` Render cron** (3rd of month, 06:00 UTC). The monthly recap is being rebuilt and nothing should go out carrying the old design. `settings.MONTHLY_RECAP_SEND_ENABLED` now defaults to **False**, so the command fails safe even if the cron fires — pausing it is belt-and-braces, and stops a pointless monthly run. **Note this stops the in-app notification too**: it is dispatched from inside the email loop, so the two cannot be separated without lifting it out. Re-enable by setting `MONTHLY_RECAP_SEND_ENABLED=True` in the environment when the rebuilt email ships. | With the recap rebuild | ☐ |


---

## Outstanding prod tasks (independent of rebuild launch)

These are already on the `main`/production path and can/should happen before cutover.

| # | Task | Action | When | Done |
|---|------|--------|------|------|
| A | **Art Reveal self-heal** — auto-completes already-revealed funder claims (attribution + email) via an event-wide sweep | Merge the self-heal PR to `main`, redeploy | Now | ☐ |
| B | ~~**Retire deprecated milestones**~~ — **OBSOLETE (2026-08).** Superseded by the Lane 2 teardown: the entire legacy milestone engine (and the `retire_milestones` command itself) was deleted. Migrations `0282` + `0283` remove the ladder-granted `UserTitle` rows and drop the tables on deploy — nothing to run by hand. **One follow-up:** after `migrate`, run `python manage.py populate_user_titles` (idempotent) to re-create any badge-granted title that happened to carry `source_type='milestone'` and was swept up. | `python manage.py populate_user_titles` (after migrate) | With the teardown deploy | ☐ |
| C | **Recompute job XP under the flat curve** — the XP-economy engine PR switches per-job leveling from the old escalating capped curve to flat cap-less (K=3,000) + T=6,000. Ledger amounts (`ContractXPGrant`) are immutable; only the level *derivation* changes, so every `ProfileJobXP.level` must be re-derived. Idempotent (rebuilds from the ledger). Run AFTER migration `0255_*` is applied. | `python manage.py recompute_job_xp --all` | Now (with the economy PR deploy) | ☐ |
| D | **Backfill community completion stats** — populates the new `Game.plats_earned_count` / `full_completion_count` / `avg_completion` columns immediately (they'd otherwise fill within the nightly `recalc_earn_rates` budget). Idempotent (recomputes from ground truth). Run AFTER migration `0256_game_avg_completion...` is applied; use a low-traffic window (full pass over ProfileGame). | `python manage.py recalc_earn_rates --max-minutes 600` | Now (with the community-stats PR deploy) | ☐ |
| E | **Universal-search trigram indexes** — migration `0257_universal_search_trgm_indexes` runs `CREATE EXTENSION pg_trgm` then builds three GIN trigram indexes with `AddIndexConcurrently` (`atomic = False`). Auto-applies on deploy; **no command, no backfill**. The only prerequisites: the DB role can create the `pg_trgm` extension (Render Postgres allows it), and the deploy tolerates a non-atomic migration. Verify it applied (`\di *_trgm` shows the three indexes). Dormant until the rebuilt navbar (`site_suggest`) ships. | Watch the deploy migrate step | Now (main PR already merged) | ☐ |
| F | **Drop the dead `GroupBadge.rarity_*` columns** — badge rarity is now derived LIVE (`badge_rarity.group_rarity`, pursuer-relative over the maintained `earned_count`), so `rarity_pct` / `rarity_class` / `rarity_rank` on `GroupBadge` are unused scaffolding. Remove the three columns in a `main` migration (they were part of the dormant schema PR). No data migration / backfill — nothing reads them. | Add a `RemoveField` migration on `main`, redeploy | With badge cutover | ☐ |
| G | **Add the `recompute_milestones` nightly cron** — the milestones page's daily freshness sweep. It's the ONLY refresh of the rarity denominator (`total_hunters`) + the earned_count drift-correction + the safety-net for the per-sync recompute. Whale-safe (streamed, bounded aggregates). Run WITHOUT `--reconcile-discord` (per-sync + on-link handle roles). Also do a one-time launch backfill (`python manage.py recompute_milestones`) after seeding so existing hunters get their earned tiers before the first cron. | Create a Render Cron Job: `python manage.py recompute_milestones`, daily 05:30 UTC (after `recalc_profile_counters`). See [cron-jobs](../../guides/cron-jobs.md). | With milestones launch | ☐ |
| H | **Share-card fonts need `collectstatic`** — the plat card rebuild adds Bricolage Grotesque (3 weights) to `static/fonts/`, and the Playwright renderer reads fonts from **STATIC_ROOT**, not `static/`. `_build_font_faces()` *silently skips* any TTF it can't find, so a deploy that misses collectstatic doesn't error — every share card just renders in a fallback face that looks almost right. No command beyond the normal build step; this row exists because the failure mode is invisible. Verify after deploy: the card's game title should be Bricolage, not Inter. | Normal `collectstatic` on deploy | With the plat card rebuild | ☐ |
| I | **Backfill the game-detail stats denorms + set the web statement timeout** — branch `fix/anon-profile-render-cost` (off `main`) moves the game-detail community stats row onto denormed columns and adds `Game.total_earns_count` / `monthly_players_count` (migration `0274_game_community_stats_denorm`). Both land at **0**, so the header's "Total Earns" and "Monthly Players" read 0 until `recalc_earn_rates` fills them — run it immediately after migrate rather than waiting for the 03:00 UTC cron. Idempotent (recomputes from ground truth); use a low-traffic window (full pass over ProfileGame + EarnedTrophy). **Separately**, set `DB_STATEMENT_TIMEOUT_MS=15000` on the **web service only** in the Render dashboard — the default stays 60000 so the worker and one-off commands are unaffected, and a value above the gunicorn worker timeout re-creates the failure mode this branch fixed. **Also in that PR, no command needed but user-visible:** migration `0275` retires the Rarest Trophies showcase and deletes existing `ProfileShowcase` rows of that type (it ranked the profile's entire earned set on a joined column — the most expensive thing an anonymous visitor could trigger). Premium users who had it will find the slot empty and free to re-fill, so it is worth a line in release notes / Discord. | `python manage.py recalc_earn_rates --max-minutes 600`, then set the env var on web | Now (with the anon-render-cost PR deploy) | ☐ |
| J | **Backfill `Game.monthly_earners_count`** — migration `0285_game_monthly_earners_count` adds the column (+ its index) for the Browse Games **Trending** sort, which now ORDERS BY it instead of aggregating ProfileGame per game on every request. It lands at **0** for every game, so until a full `recalc_earn_rates` pass completes, Trending falls through to its secondary key and reads as "most popular" rather than "trending". That degradation is deliberate (the secondary key is `-played_count`, so the order stays sensible rather than arbitrary), but it IS wrong until backfilled — and the nightly run is budget-capped with a resume cursor, so it can take several nights to reach the whole catalogue on its own. Run it immediately after migrate. Idempotent. | `python manage.py recalc_earn_rates --max-minutes 600` | With the trending-denorm deploy | ☐ |

| K | **Reconcile series titles** — `UserTitle` under-records the new badge system two ways, and both understate a title's holder count, which is now the rarity numerator on `/titles/`. (1) `grant_series_title` only runs on the `award` branch, and `diff` only awards a badge that isn't already held — so a badge earned *before* its series had a title never gets one, and re-running `evaluate_badges` cannot fix it. (2) `UserTitle` is unique on `(profile, title)` without `source_type`, so a series reusing a legacy Badge's Title got the legacy row back from `get_or_create` and recorded nothing — the hunter holds and can equip a title that reads **"Be the first"**. Symptom on beta: a title whose easiest edition ~78% of the community holds graded Mythic at 0.7%. `grant_series_title` now adopts an existing row going forward; this backfills the history. Idempotent, set-based. Run `--dry-run` first: it reports badge-holders vs countable per title. | `python manage.py sync_series_titles --dry-run` then `python manage.py sync_series_titles` | With the rarity work | ☐ |

> **`--prune` is deliberately not part of the above.** It deletes `badge_series` titles no held badge backs, which includes anything whose GroupBadge rows were dropped during re-authoring — that would silently strip earned titles. Run it only after eyeballing the orphan counts in a dry run.

---

## Post-deploy verification (milestone teardown)

`0282` deleted ladder titles by `source_id`, which misses rows with a NULL or already-dangling `source_id`
(they'd linger as bogus "Special" titles — this actually happened on dev). `0283` cleans those up by title
name. To confirm, only the three genuine one-off awards should remain:

```sql
SELECT DISTINCT t.name FROM trophies_usertitle ut
JOIN trophies_title t ON t.id = ut.title_id
WHERE ut.source_type = 'milestone';
```

Expected: nothing outside `Patron of the Arts`, `Fastest Plat in the West`, `Case Hardened`.

---

## Completed

_(Move rows here as they're done, with the deploy date, so the runbook keeps its history.)_
