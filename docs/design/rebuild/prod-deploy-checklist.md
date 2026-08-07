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
| 4 | **Backfill grouping read-models** — materializes `Genre/Theme/Franchise/Company.representative_game` (the tile + detail-hero cover) AND `Genre/Theme.related_tags` (the detail-page related rail) for the rebuilt `/genres/` list, `/genres\|themes/<slug>/` detail, `/franchises/` list + detail, AND `/companies/` list pages. The franchise cover pick honors `is_excluded`/`is_spinoff` link flags. Until it runs, tiles render the neutral placeholder and tag detail pages show no related rail. Then keep fresh via the daily cron below. | `python manage.py recompute_tag_covers` | Launch (after migrations) | Yes (recomputes from scratch; stable pick) | `/genres/` + `/franchises/` + `/companies/` tiles show the glyph placeholder; tag detail pages omit the related-tags rail | ☐ |

> **Ordering:** #1 and #2 depend on the STEP 2 schema migrations (`UserBadge.status`/`earn_rank`, `Badge` rarity fields); #3 depends on the `SeriesBadgeStanding.group_progress` migration (`0276`); #4 depends on the `Genre/Theme.representative_game` + `.related_tags` migrations (`0277`, `0278`), the `Franchise.representative_game` migration (`0279`), AND the `Company.representative_game` migration (`0280`). All run after migrations; run order between them doesn't matter. **Note:** #3 was verified on beta (evaluate_badges backfills group_progress as expected) — it still needs a run on prod at cutover.

### Cron / scheduling

| # | Task | When | Done |
|---|------|------|------|
| 1 | **Register `recompute_tag_covers` cron** — new daily Render Cron Job (`python manage.py recompute_tag_covers`, 03:45 UTC) keeping the genre/theme tile covers fresh as games sync. Documented in [cron-jobs.md](../../guides/cron-jobs.md). The one-time backfill is task #4 above; this is the ongoing schedule. | Launch (Render dashboard) | ☐ |

### Manual config (dashboards, env, third-party)

| # | Task | When | Done |
|---|------|------|------|
| _(none yet)_ | | | |

---

## Outstanding prod tasks (independent of rebuild launch)

These are already on the `main`/production path and can/should happen before cutover.

| # | Task | Action | When | Done |
|---|------|--------|------|------|
| A | **Art Reveal self-heal** — auto-completes already-revealed funder claims (attribution + email) via an event-wide sweep | Merge the self-heal PR to `main`, redeploy | Now | ☐ |
| B | **Retire deprecated milestones** — hides the dead checklist/review criteria-types (`checklist_upvotes`, `review_count`, `review_helpful_count`) from the milestones page and stops awarding them, and removes the titles they granted (earned `UserMilestone` records are preserved). Destructive on `UserTitle` rows; idempotent (re-runs are no-ops). Requires migration `0254_milestone_is_active` applied. | Dry-run first to review counts: `python manage.py retire_milestones checklist_upvotes review_count review_helpful_count` — then commit: `... --apply` | Now | ☐ |
| C | **Recompute job XP under the flat curve** — the XP-economy engine PR switches per-job leveling from the old escalating capped curve to flat cap-less (K=3,000) + T=6,000. Ledger amounts (`ContractXPGrant`) are immutable; only the level *derivation* changes, so every `ProfileJobXP.level` must be re-derived. Idempotent (rebuilds from the ledger). Run AFTER migration `0255_*` is applied. | `python manage.py recompute_job_xp --all` | Now (with the economy PR deploy) | ☐ |
| D | **Backfill community completion stats** — populates the new `Game.plats_earned_count` / `full_completion_count` / `avg_completion` columns immediately (they'd otherwise fill within the nightly `recalc_earn_rates` budget). Idempotent (recomputes from ground truth). Run AFTER migration `0256_game_avg_completion...` is applied; use a low-traffic window (full pass over ProfileGame). | `python manage.py recalc_earn_rates --max-minutes 600` | Now (with the community-stats PR deploy) | ☐ |
| E | **Universal-search trigram indexes** — migration `0257_universal_search_trgm_indexes` runs `CREATE EXTENSION pg_trgm` then builds three GIN trigram indexes with `AddIndexConcurrently` (`atomic = False`). Auto-applies on deploy; **no command, no backfill**. The only prerequisites: the DB role can create the `pg_trgm` extension (Render Postgres allows it), and the deploy tolerates a non-atomic migration. Verify it applied (`\di *_trgm` shows the three indexes). Dormant until the rebuilt navbar (`site_suggest`) ships. | Watch the deploy migrate step | Now (main PR already merged) | ☐ |
| F | **Drop the dead `GroupBadge.rarity_*` columns** — badge rarity is now derived LIVE (`badge_rarity.group_rarity`, pursuer-relative over the maintained `earned_count`), so `rarity_pct` / `rarity_class` / `rarity_rank` on `GroupBadge` are unused scaffolding. Remove the three columns in a `main` migration (they were part of the dormant schema PR). No data migration / backfill — nothing reads them. | Add a `RemoveField` migration on `main`, redeploy | With badge cutover | ☐ |

---

## Completed

_(Move rows here as they're done, with the deploy date, so the runbook keeps its history.)_
