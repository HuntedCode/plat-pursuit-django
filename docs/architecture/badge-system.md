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

### Scope, gating, satisfaction

Three different questions, three different answers — this is the part of the engine most often misread.

| Question | Reads | Rule |
|---|---|---|
| Is the stage **in scope** for this edition? | platforms | it holds at least one game on the edition's platforms |
| Does it **gate** (become required)? | the edition's own games | one of THOSE is obtainable, and not delisted-in-an-excluding-group |
| Is it **satisfied**? | **every game in the stage** | the hunter completed any of them, on any platform |

**Satisfaction is cross-platform; gating is not.** Clear a stage on PS5 and it counts for Legacy HD too —
one stage is one *work*, and a cross-gen hunter should not have to buy and replay the same game once per
edition. But an alive PS3 list can never make a stage *required* of Ultra HD, because a badge must not
demand work that cannot be done on its own platforms.

A stage with nothing on the edition's platforms is **out of scope**: not gating, not satisfiable, and
paying no XP there. That is the one limit on cross-platform credit.

### What "completed" means: the two bars

The table above says a stage is satisfied when the hunter "completed" a game. There are exactly two
completion bars, supplied per game by the orchestrator, and **neither branches on whether the game has a
platinum** — the engine never asks.

| Bar | Source | Meaning |
|---|---|---|
| `base_complete` | `ProfileTrophyGroup.progress == 100` on the game's **`default`** trophy group | The **platinum** on a plat game; the **base trophy list at 100%** on a game without one. **DLC-independent** |
| `full_complete` | `ProfileGame.progress == 100` | The whole game at 100%, **DLC included** |

`base_complete` is what earns the badge. `full_complete` only sets `is_holo`, which is cosmetic, flips
both ways and pays no XP.

**A game with no platinum still has to be finished** — its base list must reach 100%, not merely be
played. What it does *not* have to do is clear DLC, because `default` is PSN's base group and the DLC
groups (`001`, `002`, …) sit outside it. So the no-plat requirement is exactly as demanding as the
platinum requirement, measured on the list that actually exists. This is also what the hunter is told, in
`templates/trophies/badge_how_it_works.html`: *"A game with no platinum counts the moment its base trophy
list hits 100%, so nothing in a set is unwinnable."*

One invariant is enforced in the orchestrator rather than the engine: `base_complete = base_prog == 100 or
full_complete`. A missing or stale default `ProfileTrophyGroup` row must never be able to produce "holo
without base".

A `ConceptBundle` collapses to a single synthetic game and is held to the same two bars, but **every**
member must meet them — see the `ConceptBundle` docstring in `trophies/models.py`.

> **Changed 2026-09** (owner's call). Satisfaction used to be scoped to qualifying games the way gating
> still is, so each edition had to be cleared on its own platform. The per-edition *independence* that
> created is gone: two editions are separate chases now only when their stages don't overlap platforms.

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

- `XP_PER_STAGE = 500` per **in-scope** stage cleared — gating or not (see below)
- `XP_BADGE_COMPLETION_BONUS = 600` once, when the base badge is earned
- Holo is worth nothing
- Stage 0 pays nothing, ever. It is tangential by definition, and paying for it would make optional work
  feel mandatory.

**XP and requirements came apart in 2026-09.** A stage that stopped gating — its qualifying games went
unobtainable, or delisted in an excluding group — still pays whoever cleared it while it was alive. Points
are for work done, and clawing them back because a storefront closed punishes the hunter for someone
else's decision. The engine carries two counts for this reason, and they must not be confused:

| Field | Counts | Feeds |
|---|---|---|
| `base_satisfied_count` | **gating** stages cleared | the progress fraction ("3 of 5"), which must never exceed its denominator |
| `xp_stage_count` | every **in-scope** stage cleared | XP |

The corollary: a badge with no gating stages left is **revoked** (unearnable, so the hold is deleted) and
still pays its stage XP. Only the completion bonus is withheld — that one is for finishing a badge that
can no longer be finished.

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
| Claiming | refused in BOTH places: excluded from `series_needing_artwork()` (the picker) AND rejected by `claim_badge()` (the thing that takes the money, which reads `series_id` straight from the POST). A donor who wants that subject drawn claims the SOURCE, and both light up |
| Other write paths | `ArtRevealItem.release()` skips a borrowing series -- writing there would end the borrow silently and credit the art to nobody. `clean()` refuses a link while an OPEN claim exists, which would otherwise strand the donor and push the fundraiser bar past 100% |
| Partial art | resolution keys on the MAIN image at both levels, never `main OR holo`. Partial art is the norm (`ArtRevealItem.release()` writes `badge_image` alone), so an OR let a holo-only edition override wipe a borrowed main image back to the placeholder |
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

**The two art write paths have been repointed; `BadgeAdmin` is gone with them.** The fundraiser
(`donation_service.complete_badge_claim`) and `art_reveal.ArtRevealItem.release()` both used to write the
legacy `Badge` table -- `funded_by` and `badge_image` on a row nothing renders, so a donor who funded
artwork was credited invisibly. Both now write `BadgeSeries`, which is what `GroupBadge.art_layers()` and
`effective_funded_by` actually resolve through. `BadgeAdmin` was retained only because `art_reveal`'s
inline autocompleted against `Badge` and dropping the registration fails the ENTIRE admin site's system
check with `admin.E039`, not just art_reveal; that inline points at `BadgeSeries` now, so the registration
went too. Legacy `Badge` / `UserBadge` rows are still READ in several places (titles, job-board coverage,
company pages) and are retained for rollback and audit.

**Scope by SERIES, never by badge.** `recompute_standing` REPLACES a series' standing from only the
editions it is handed. Evaluate one edition of a two-edition series and the other's XP silently becomes
zero. Every entry point resolves to series, then to all live editions of them.

**`base_satisfied_count` is NOT the XP numerator.** It counts gating stages, so it is the progress
fraction's numerator and is structurally `<= gating_count`. XP reads `xp_stage_count`, which counts every
in-scope stage cleared. Wiring XP to `base_satisfied_count` silently stops paying for stages that went
unobtainable; wiring progress to `xp_stage_count` produces bars over 100%.

**Cross-platform satisfaction means an edition can be 'started' by work on another platform.** Any test or
fixture that needs two INDEPENDENT editions must give them stages that do not overlap platforms — the
shared-stage shape no longer produces an untouched edition. See `_split_edition_series` in
`tests/engine/test_badge_xp.py`.

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

## Curator authoring in admin

Two affordances exist on the admin side because the models are shaped for the engine, not for the person
filling them in.

**The subject pickers are scoped by field.** `Franchise` holds IGDB franchises AND IGDB collections in
one table (separate ID namespaces, one model), so an unscoped autocomplete offers "Resident Evil" twice
with nothing to tell the rows apart, and `BadgeSeries.franchise` / `.collection` get filled in
interchangeably. The scope is `limit_choices_to` on the model fields, NOT an admin hook: Django applies it
in `AutocompleteJsonView` *and* in form validation, so it covers what the picker offers and what a posted
id is allowed to be. The previous admin-side version keyed on the request's `model_name == 'badge'` and
silently stopped filtering the moment the rebuild renamed the model to `badgeseries` -- a filter that
fails open, with no error, is the reason this lives on the field now. Other `Franchise` autocompletes
(e.g. `ConceptFranchise.franchise`) still see both types deliberately; IGDB lists both kinds of link.

**Stages duplicate onto a new slug.** `Stage` joins to a series by a bare `series_slug` string, so a
franchise badge that mirrors a series badge means re-entering the same concept picks stage by stage.
`StageAdmin`'s "Duplicate selected stages under a new series slug" action copies a selection wholesale:
number, title, icon, required tiers, the online flag, the standalone concepts, and each `ConceptBundle`
with its own members. The originals are untouched, the whole run is one transaction (a committed stage
with no concepts reads to the engine as instantly satisfied, not as broken), and a stage number already
present on the target slug is SKIPPED and named in the message rather than renumbered -- renumbering would
quietly produce a stage list that is not the one the curator copied. The slug is re-slugified on the way
in, because `SlugField` accepts uppercase and `FromSoft` would save fine and then join to nothing.

`stage_icon` is copied and then re-derived: it is a denorm of the first concept's cover, maintained by
`auto_populate_stage_icon`, and setting the copy's concepts fires that signal. The copy is what preserves
a hand-set icon on a stage with no concepts to re-derive from, the one case the signal never touches.

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
