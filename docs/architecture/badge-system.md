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
| Stages | `Stage` (+ `ConceptBundle`, `Stage.trophy_groups`) | Series-level. Every edition works the same stage list |

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
| `base_complete` | `ProfileTrophyGroup.progress == 100` on the game's **`default`** trophy group, **or** `full_complete` (see the invariant below) | The **platinum** on a plat game; the **base trophy list at 100%** on a game without one. **DLC-independent** |
| `full_complete` | `ProfileGame.progress == 100` | The whole game at 100%, **DLC included** |

`base_complete` is what earns the badge. The only thing `full_complete` earns is `is_holo`, which is
cosmetic, flips both ways and pays no XP. It is not inert otherwise, though: it also back-fills
`base_complete` (the invariant below) and dates the earn in the same case.

The date rule reads its branch off **progress, not off date presence**, and the difference is worth
stating because it is easy to describe backwards:

```python
completion_date = base_date if base_prog == 100 else (full_date if full_complete else None)
```

(The line lives in the `game_state` closure in `evaluate_with_catalog`.)

So `full_date` is used only when the default group is SHORT of 100% **and** the game is fully complete —
the same case in which `full_complete` is what made `base_complete` true. When the default group IS at
100%, its own date wins **even when that date is null**: a stale `ProfileTrophyGroup` with no
`last_trophy_at` yields a game that is complete and undated, and `full_complete` does not rescue it.

An undated game drops out of its stage's `base_dates`, and `_earned_date` can then return None for the
whole badge. Under `completion_policy='all'` it always does, because the threshold is the satisfied
count; under `min_count` it need not, because the threshold is `min_required` and other dated stages can
still meet it.

**A None earn date is not a one-off cosmetic loss, and this is the part worth knowing.** `diff()`
compares `cur.earned_at` against `res.earned_date`, so a stored date against a live None mismatches,
emits an `update`, and `apply_changes` writes `earned_at = ch.earned_date or timezone.now()`. The next
evaluation compares the same pair and mismatches again. The badge is re-stamped to sync time **on every
run, forever**, which churns its position on the earners board. `tests/engine/test_badge_engine.py`
records this as the failure mode behind the cross-platform `base_date` widening.

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

### Trophy groups as stage members

Some lists carry whole games as DLC trophy groups (the Kingdom Hearts PS5 collections ship Re:Chain of
Memories, KH II and others as groups of one list, none of them needed for its platinum). Finishing one of
those never shows up as a finished list, so `Stage.trophy_groups` (M2M to `ConceptTrophyGroup`) lets a stage
count ONE group on its own. The owner's rules (2026-10-24):

| Rule | How |
|---|---|
| The group at 100% clears the stage **and** counts for holo | `group_state` in `evaluate_with_catalog`: both bars read the group's own `ProfileTrophyGroup`, because the group is the whole game it stands for |
| The list at 100% counts too | the same invariant as `game_state`: `ProfileGame.progress == 100` means every group in it is done, so a stale group row cannot deny credit |
| No cross-credit, either way | the list's platinum (its `default` group) never clears a group's stage, and a group never clears a stage that lists the whole list |
| Gating is the list's | each version carrying the group is a unit with that version's platforms, obtainable and delisted status, like any game |
| A version without the group cannot clear it | a CTG is concept-level; `group_members` pairs it with each version's own `TrophyGroup`, and a version lacking it is not a unit |

One unit per version, built in ONE place, `badge_orchestrator.stage_units`, which the required-stage count,
every evaluation and the badge page's scope check all call.

**The read.** A group's progress comes from its own small `ProfileTrophyGroup` read over
`catalog['group_tg_ids']`, an explicit id list in both regimes, and no query at all when no stage has a group.
It never rides the `whole_catalogue` subquery: that statement runs once per profile on the nightly sweep, and
routing groups through it joined every concept's trophy groups into all ~300,000 of them. Only whole-list
games feed the default-group (base) read, so a list that is on a stage just for one of its groups never reads
its own base bar.

**Authoring.** Pick the group in the stage's "Trophy groups" box in admin. A list may not also be on the
same stage as a whole list or a bundle member (`StageAdminForm.clean`): the two would set different bars for
one list. To count the game in a collection's MAIN group on its own (so holo means that game at 100%, not the
whole collection), add the list's `default` group here instead of the list itself. Its card is then named with
PSN's own name for that group (the `default` CTG is always called "Base Game"), else the list's title. The
bundle inline refuses a list whose group is on the stage too (`ConceptBundleInlineFormSet`). Both checks read
the stage's SAVED members, as the bundle-vs-standalone check always has, so a change to both in one save is
checked against the other's previous state. `generate_badge_stages --append` never writes onto a stage that
holds a list through a group; it reports the addition for a hand edit, as it does for bundles.

**On the badge page** a group stage says what it is three times, because a group card on its own reads as the
whole game (owner, 2026-10-25): the collapsed header counts "1 trophy group" when that is all the stage holds,
the opened stage leads with one sentence per group ("Only the trophies in X count here, not the rest of Y"),
and the card's art carries a "Trophy group" label. A group card is named for the group, shows its icon, says
which list it ships in ("Part of Y"), and
reads the viewer's progress on the group (its `pgame` is the `ProfileTrophyGroup` row). It carries no contract
band and no platinum chip, since both are the whole list's. Its ratings are the group's own. My Stats counts
the group as a game of its own (as the hero's "N games" does) with the group's trophies, never the list's;
it skips the trophies only when the list is also on screen whole, which already counted them. The list's
play time cannot be split, so a group adds none, and a group is "platted" only when it holds an earned
platinum, as a whole list without one never is.

**Questions about the list itself do NOT follow the group path** (`lists_only=True` on the helpers): the
art-reveal community platinum count, job-board coverage, contract job tags, the taxonomy report, the
completion card, and the Challenge Optimizer. There the concept is a whole collection, a different game, so
its platinum is not badge work and its contract does not cover the stage. Everything that asks which badges a
game counts toward DOES follow it: the engine, sync, the DLC sweep, `evaluate_badges --series`, the coverage
audit, the Game page's "Counts toward", the browse badge pills and "In a badge" filter, and the company
filter.

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
| `trophies/services/badge_coverage_service.py` | Curator audit: games missing from a series' stages. `series_candidates()` is THE definition of a series' games (audit + stage generator) |
| `trophies/services/badge_stage_service.py` | `generate_badge_stages`: plans/writes one stage per origin game |
| `trophies/services/badge_candidate_service.py` | `badge_candidates`: franchises/series with 2+ live-contracted games GATING on one edition, ranked by average game-page players. Counts in the unit of a stage: origin games (`origin_key`; a bundle-only contract is one game), gating by `evaluate_stage`, membership as `series_candidates()`. Also owns the "subject already has a badge" rule, in two shapes kept in step: `existing_badge` (one subject, `build_badge` refuses on it) and `_subjects_with_badges` (the catalogue, `badge_candidates` hides on it) |
| `trophies/services/developer_family_service.py` | A developer badge's studio family (`developer_family_ids`), its developer credits, and which studio a game or stage is filed under (`filing_studio`, `concepts_filing_studio`). Shared by the audit, the stage generator and the badge page's studio sections |
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

**A stage reaches its games three ways, and every membership query must follow all three.** Direct
(`Stage.concepts`), bundled (`concept_bundles -> concepts`) and trophy group (`trophy_groups -> concept`).
Use `trophies/services/stage_membership.py` (`in_series_q`, `stage_has_concept_q`, or
`concept_series_paths()` / `stage_concept_paths()` to read through each path), with `lists_only=True` for a
question about the list itself (see the trophy-group section). Matching only the first path misses every
bundled game, which was a real bug in both engines; retyping the paths at a call site is how the next one
would happen. Not yet moved over, and still reading `Stage.concepts` alone: the site-wide unique-badge-games
stat (`core/services/stats.py`).

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
number, title, the standalone concepts, the trophy groups, and each `ConceptBundle`
with its own members. The originals are untouched, the whole run is one transaction (a committed stage
with no concepts reads to the engine as instantly satisfied, not as broken), and a stage number already
present on the target slug is SKIPPED and named in the message rather than renumbered -- renumbering would
quietly produce a stage list that is not the one the curator copied. The slug is re-slugified on the way
in, because `SlugField` accepts uppercase and `FromSoft` would save fine and then join to nothing.

**Stages can be generated.** For franchise, series and developer badges the stage list follows a formula
(one stage per origin game, oldest first or by studio; a standalone expansion such as Death of the
Outsider is its own origin game, so its own stage), so `generate_badge_stages <series_slug>` builds it
from the series' franchise/collection/developer and the curator tweaks from there. It reads the same
`series_candidates()` the nightly audit checks, so a generated badge audits clean, and `--append` closes an
audit gap, except one whose origin sits in a bundle stage, which it lists for a hand edit instead. Bundles
are never generated. See
[management-commands](../guides/management-commands.md) and `trophies/services/badge_stage_service.py`.

**Stage icons are derived, not stored.** The badge page draws each stage's icon from one of its versions
(`badge_detail_service._stage_icon_url`), ranked in this order (owner's call):

1. **Real cover art** (`Game.has_cover_art`). The icon is a picture of the game, so a delisted version's
   real cover beats the generic PlayStation title icon. If NO version has real art, the stage shows no
   icon at all, rather than a placeholder cropped into the small square.
2. **Still obtainable on that edition**, by the same `_display_playable` rule as the stage's grid (bundle
   members included), so the icon can differ between edition tabs.
3. **Most recently released**: that version's trusted IGDB first PlayStation release, else its PSN date,
   undated last. Ties go to the highest game id.

Every game it reads is already loaded for the stage's cards, so it costs no queries. It replaced a stored
`Stage.stage_icon` that two m2m signals kept as a copy of whichever concept came first, which made the icon
arbitrary and made every stage edit (a duplicate, an append) fight the signal to keep a hand-set one. There
is no way to hand-set an icon now: to change it, change the stage's games.

**Developer badge pages split by studio.** On a developer badge (`is_developer_badge`), an edition whose
stages span 2+ studios shows one ladder per filing studio: the developer first, then its studios A-Z, then
"Other studios" for stages no family studio developed. A stage is filed as one unit over all its games,
bundle members included, by `concepts_filing_studio`, the same rule `generate_badge_stages` orders by.

- **Numbers run on across the sections**, in the order shown, and "Up next" follows that order. The
  requirements tile grid stays one flat grid in the same order. Within a studio, the authored order holds.
- **Retired stages keep their one fenced section**, labelled by studio inside it (owner's call: a fence per
  studio would break up the live ladders and, on a dead edition, leave nothing but fences). The 2+ studio
  test counts retired stages, so a studio whose only stages are retired is still named.
- **Only the badge page pays for it**: `get_badge_detail(studio_sections=True)`. The medallion peek and the
  collection modal leave it off. On a non-developer badge it costs nothing; on a developer badge, a few
  small queries bounded by the studio family's depth and the badge's own games, never the family's
  catalogue (`_stage_studios`).
- **Each studio header shows its IGDB logo** (on the company page's light plate, else a building glyph) and
  links to its company page; "Other studios" links nowhere. Company slugs are stored as IGDB sends them,
  never slugified, so the link is built in the service (`_company_url`) and dropped when the slug makes no
  URL, rather than raising `NoReverseMatch` and taking the whole badge page down.
- **Two studios with the same name** (`Company.name` is not unique) get two adjacent sections with the same
  header: they are filed by company, not by name.
- **An `--append`ed stage moves into its studio on the page** even though its stage number is after the
  last one, so on an appended badge the display order and the authored numbers differ.

## Management Commands

| Command | Usage | Purpose |
|---|---|---|
| `evaluate_badges` | `<username>`, `--all`, `--series <slug>`, `--dry-run`, `--compare-legacy` | The runner. Nightly `--all` is the reconcile that keeps every figure honest |
| `audit_badge_coverage` | `--dry-run`, `--always` | Emails franchise/collection/developer series that are missing games (developer = studio + subsidiaries + former identities, developed credits only), plus developer series pinned to a renamed company |
| `generate_badge_stages` | `<series_slug>`, `--append`, `--dry-run` | Builds a series' stages, one per origin game (see [management-commands](../guides/management-commands.md)) |
| `convert_series_to_groups` | see `--help` | Cutover seeding: builds `BadgeSeries` + `GroupBadge` from the legacy rows |
| `badge_candidates` | `--limit`, `--include-existing` | Read-only: which franchises/series would make a good badge (2+ live-contracted games on one edition), best attended first |
| `build_badge` | `--franchise`, `--series`, `--developer`, `--slug`, `--dry-run` | One-shot authoring: creates the `BadgeSeries`, runs `generate_badge_stages`, then `convert_series_to_groups` (hidden editions), in one transaction |

## Related Docs

- [badge-backend-rebuild.md](../design/rebuild/badge-backend-rebuild.md): design + cutover record
- [leaderboard-system.md](leaderboard-system.md): the board reads
- [gamification.md](gamification.md): the other XP economy (jobs / Pursuer Level)
