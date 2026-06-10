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

> **Ordering:** both depend on the STEP 2 schema migrations (`UserBadge.status`/`earn_rank`, `Badge` rarity fields) being applied first. Run order between the two doesn't matter.

### Cron / scheduling

| # | Task | When | Done |
|---|------|------|------|
| _(none yet)_ | | | |

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

---

## Completed

_(Move rows here as they're done, with the deploy date, so the runbook keeps its history.)_
