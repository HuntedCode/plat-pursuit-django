# Badge System

Badges reward hunters for completing curated sets of PlayStation games. A **series** (e.g. Soulsborne,
Capcom) defines the theme and the stages; a **group badge** is the separately earnable badge for one
platform edition of that series. Earning one grants XP, a title, and a Discord announcement.

> **Rewritten 2026-08.** This replaced the tier-based engine (`Badge` tiers 1-4, `UserBadge`,
> `UserBadgeProgress`, `badge_service`, `xp_service`, Redis leaderboards), removed in badge cutover 5b.
> Design and cutover history: [badge-backend-rebuild.md](../design/rebuild/badge-backend-rebuild.md).
> The legacy `Badge` / `UserBadge` tables are RETAINED for rollback and audit. No BADGE-ENGINE code writes
> them, but two live features still do and were never repointed: the **artwork fundraiser**
> (`donation_service` writes `Badge.funded_by` when a donation completes) and **art_reveal**
> (`ArtRevealItem.release()` writes `Badge.badge_image`). `BadgeAdmin` also permits manual writes and is
> deliberately retained. See Gotchas below.

## Architecture Overview

### Series x edition, not tiers

The old model gave each series four tiers of escalating difficulty over one game list. The current model
splits a series by **platform group** instead: Ultra HD (PS4/PS5) and Legacy HD (PS3/Vita) are different
badges over the same stages, because they are genuinely different games to hunt.

| Concept | Model | What it is |
|---|---|---|
| Series | `BadgeSeries` | Theme, art, title, completion policy. One row per `series_slug` |
| Edition | `PlatformGroup` | A platform set (`key`, `platforms`, `exclude_delisted`) |
| The badge | `GroupBadge` | `BadgeSeries` x `PlatformGroup`. The earnable thing |
| A hold | `UserGroupBadge` | Binary: the row exists iff the hunter currently meets the bar |
| Stages | `Stage` (+ `ConceptBundle`) | Series-level. Every edition works the same stage list |

**Holds are binary.** There is no `maintenance` state: a revoke DELETES the row. Rank is derived live
from `earned_at` among current holders, so if a series grows, whoever first clears the harder iteration
takes #1.

**`is_holo`** is a live cosmetic flag (100% including DLC on every gating stage). It flips both ways and
is worth no XP.

### Gating vs satisfaction

A stage is **satisfied** if the hunter completed ANY qualifying game in it. A stage **gates** an edition
only if it holds a game that is obtainable within that edition's platform group. So a stage whose only
game is PS3-exclusive gates Legacy HD but not Ultra HD, and the same series can require different work in
each edition without any per-edition stage authoring.

`completion_policy` is `all` (every gating stage) or `min_count` (megamix: `min_required` of them).

## File Map

| File | Responsibility |
|---|---|
| `trophies/services/badge_engine.py` | PURE evaluation. No ORM. Stage/group inputs -> `GroupBadgeResult` |
| `trophies/services/badge_apply.py` | The ORM seam: plan, diff, apply, announce, recompute. Owns `earned_count` |
| `trophies/services/badge_xp.py` | XP + progress model. Writes the three standing tables |
| `trophies/services/badge_adapters.py` | Side effects: titles, events, the Discord announcement |
| `trophies/services/badge_leaderboards.py` | All board reads ("Lane B") |
| `trophies/services/collection_service.py` | The hunter's Collection wall + `closest_badge` |
| `trophies/services/badge_coverage_service.py` | Curator audit: games missing from a series' stages |
| `trophies/management/commands/evaluate_badges.py` | The only runner: one hunter, `--all`, `--series`, `--dry-run` |

## Entry points

| Caller | Function | Notify? |
|---|---|---|
| Sync (`_job_sync_complete`) | `evaluate_for_sync(profile, pg_ids)` | Yes |
| Discord link / PSN verify | `evaluate_and_apply(profile, notify=True)` | Yes |
| Bot `/recheck-badges` | `evaluate_and_apply(..., notify=False)` | No (the bot replies with the deltas) |
| Nightly cron | `evaluate_badges --all` -> `evaluate_and_apply_batch` | No |
| Admin action | `evaluate_and_apply` per selected profile | No |
| DLC detection | `evaluate_and_apply_batch` | No |

## XP

Flat and deliberately simple, all constants in `badge_xp.py`:

- `XP_PER_STAGE = 500` per gating stage cleared
- `XP_BADGE_COMPLETION_BONUS = 600` once, when the base badge is earned
- Holo is worth nothing

XP accrues **per group badge**, so a two-edition series is worth twice a one-edition series. It sums into
`SeriesBadgeStanding` (per series) and `ProfileBadgeStanding` (grand total), with `ProfileEditionStanding`
holding the same totals sliced per edition to back the boards' edition filter.

`recompute_standing` writes a fourth store in the same pass: `SeriesEditionStanding`, one row per (profile,
series, STARTED edition), carrying that edition's points AND its own `advanced_at`. It costs no extra
EVALUATION -- the loop already holds each edition's `GroupBadgeResult`, and `_advanced_at` is a pure
function of one of those, so the per-edition date was always derivable; `compute_series_standings` simply
only ever asked for the furthest-along edition's. It backs badge detail's per-edition board; see
[leaderboard-system.md](leaderboard-system.md).

Calibrated to the "1,000,000 Club": over a projected mature catalog (~400 group badges, ~5 gating stages
each) a completionist lands ~1.24M. See `test_million_club_calibration`.

## Shared artwork between sister series

A franchise badge and a series badge are often the same subject wearing two labels -- "God of War"
the franchise and "God of War" the series. `BadgeSeries.artwork_source` (self-FK, `SET_NULL`) says
*display that series' art instead of holding your own*.

| | |
|---|---|
| Resolution | per-edition override -> the series' own art -> `artwork_source`'s art -> user-badge avatar -> static default |
| Funder credit | **travels with the image.** `GroupBadge._artwork_origin()` decides once which series the art comes from, and `art_layers()`, `effective_holo_image` and `effective_funded_by` all read it |
| Claiming | a series with `artwork_source` set is **excluded** from `DonationService.series_needing_artwork()`. A donor who wants that subject drawn claims the SOURCE, and both badges light up |
| Depth | ONE hop, enforced in `clean()`: no self-reference, the source may not itself borrow, and a series others borrow from may not start borrowing |

**Why not derive it from the shared `franchise` FK**, which looks free:

1. A franchise has SEVERAL sister series (God of War 2018 and Ragnarok are separate series badges),
   so derivation has no deterministic answer for whose art wins -- it would silently pick one and
   change its mind when rows are added.
2. `BadgeSeries.franchise` already means something load-bearing and different. `audit_badge_coverage`
   reads it as *"this series is expected to cover every non-excluded game in that franchise"*, so
   setting it on a series badge to express sisterhood would flag every other franchise game as a
   coverage gap -- by email, daily. **Two facts, two fields.**

The claim guard keys on the LINK, not on the lender having art yet. Keying on "the lender has an
image" would leave the borrower claimable in precisely the window a donor would claim it: before the
art lands.

## Gotchas and Pitfalls

**Two live features still write the legacy `Badge` table, and one of them is a payment flow.**
`fundraiser/services/donation_service.py` credits a donor with
`Badge.objects.filter(series_slug=...).update(funded_by=...)` when an artwork donation completes, and the
fundraiser's badge picker reads `Badge.objects.live().filter(tier=1)`. But the medallion renders
`GroupBadge.effective_funded_by`, which resolves `funded_by_override or series.funded_by` -- neither of
which the fundraiser touches. **A donor who funds artwork today is credited on a row nothing displays.**
`art_reveal.ArtRevealItem.release()` similarly writes `Badge.badge_image`. Repointing both onto
`BadgeSeries` is outstanding work, and it is what actually retires the tier model; `BadgeAdmin` cannot be
deleted until then (see below).

**`BadgeAdmin` is retained deliberately.** `art_reveal.ArtRevealItem` has a live FK to `Badge`, and its
inline's `autocomplete_fields` requires a registered admin for the model. Deleting the registration fails
the ENTIRE admin site's system check with `admin.E039`, not just art_reveal.

**Scope by SERIES, never by badge.** `recompute_standing` REPLACES a series' standing from only the
editions it is handed. Evaluate one edition of a two-edition series and the other's XP silently becomes
zero. Every entry point resolves to series, then to all live editions of them.

**Bundled games are not in `Stage.concepts`.** A concept is either a direct stage member or a
`ConceptBundle` member on that stage, never both. Any query that finds "the series a game belongs to"
must check `Q(concepts=...) | Q(concept_bundles__concepts=...)`. Matching only the first misses every
bundled game, which was a real bug in both engines.

**Editions overlap for trophies but not for badges.** A cross-gen game counts toward both editions'
trophy figures, but a `GroupBadge` belongs to exactly one `PlatformGroup`. So per-edition badge counts sum
to the total and per-edition trophy counts do not.

**Announcements are at-most-once, and need to be.** Because a hold is binary, a revoke-then-re-earn is
indistinguishable from a first earn. `GroupBadgeAnnouncement` records every (hunter, badge) ever
announced and is never deleted. A Redis cooldown is not a substitute: any TTL short enough to be a
cooldown has expired by the time year-later PSN flux re-triggers the earn.

**Announce BEFORE `recompute_standing`.** `apply_changes` is atomic and has committed; the recompute is
not and can time out. Announcing after it meant a timeout swallowed the announcement permanently, since
`awarded` is a transition that never fires again.

**`earned_count` is a manual denorm owned by `apply_changes`** (no signals). The revoke decrement is
clamped with `Greatest(..., 0)`: the column has a `>= 0` check constraint and the apply is one
transaction, so unclamped drift aborted the whole evaluation, not just the counter.

**A `Profile` delete bypasses `apply_changes`.** The cascade drops holds without decrementing, so
`reconcile_group_badge_earned_counts_on_profile_delete` (a `pre_delete` signal) handles it.

**`is_live` gates evaluation AND every figure.** A dormant edition is invisible to XP, to `badges_held`,
to the digest and to the community stats. Counting held rows without that filter made a curator's
smoke-test badge show up in a real hunter's totals.

## Management Commands

| Command | Usage | Purpose |
|---|---|---|
| `evaluate_badges` | `<username>`, `--all`, `--series <slug>`, `--dry-run`, `--compare-legacy` | The runner. Nightly `--all` is the reconcile that keeps every figure honest |
| `audit_badge_coverage` | `--dry-run`, `--always` | Emails franchise/collection/developer series that are missing games |
| `convert_series_to_groups` | see `--help` | Cutover seeding: builds `BadgeSeries` + `GroupBadge` from the legacy rows |

## Related Docs

- [badge-backend-rebuild.md](../design/rebuild/badge-backend-rebuild.md): design + cutover record
- [leaderboard-system.md](leaderboard-system.md): the board reads
- [gamification.md](gamification.md): the other XP economy (jobs / Pursuer Level)
