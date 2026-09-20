# Community Polls

**Status: design. Nothing built.** Supersedes [tier-lists.md](tier-lists.md) and the `prompts` app
built against it (see [Why not `prompts`](#why-not-prompts)).

A poll the team posts, the community answers, and everybody sees the results afterwards. Modelled on
the Wii's Everybody Votes Channel: you pick your answer, you **predict what the majority will say**,
and when the poll closes both are revealed. Being right about the community is a stat you accumulate.

## Why this and not user-authored tier lists

The `prompts` app (Tiers, Grids & Polls) was built to let hunters author tier lists, grids and polls
for each other. It works, it is tested, and it is parked unmerged, because it failed a question worth
asking earlier than we did: **what about it needs PlatPursuit?**

Nothing did. A tier-list maker over a game catalogue is tiermaker.com; a grid is grids.fun, which the
original plan named as its own reference. The rest of this site is *earned* — badges, contracts,
career, rank, the Pursuer Card all derive from trophies somebody actually pulled — and a tier list is
an opinion produced in thirty seconds about games the author may never have touched. It was the one
surface that would survive deleting the trophy data.

The tell was in its own copy. Three of the four suggested prompt titles it shipped with were about
the hunter's trophy life ("Rank the platinums you actually enjoyed", "The one you would replay
tomorrow") while the data model let anyone pick any game in the catalogue. The feature knew what it
wanted to be and the schema did not follow.

**This version inverts the authoring.** The team writes the questions, which is how the rest of the
site already works: staff write the roadmaps and the guides, hunters answer them. That deletes the
entire user-generated-content surface in one move — no authoring caps, no banned-word filtering, no
restriction gates, no moderation queue for other people's opinions, no soft deletes, no per-prompt
freeze table. And the prediction mechanic gives it the register the rest of the site is in: a score
you earn by knowing your community.

## Why not `prompts`

`prompts` is seven models and roughly two thousand lines of service, and almost all of it exists to
answer "a stranger authored this": ownership checks, the publish floor, the freeze table, the
restriction gate, the banned-word filter, soft delete, per-tier authoring caps. In a staff-authored
feature nobody is a stranger, so every one of those is maintenance with no user.

The parts genuinely worth carrying forward are **three lessons, not code**:

1. **A vote points at the option ROW, never at the catalogue.** `PromptPlacement` FKs `PromptGame`
   rather than `Concept` precisely so that removing an option cascades its votes instead of leaving
   dangling references that still count. Same rule here.
2. **"One vote per hunter" is a database constraint or it is not a rule.** `prompts` learned this the
   hard way: `unique(response, bucket) WHERE single_slot` holds only while a poll has exactly one
   bucket, because the predicate is per-row and the count lives on the parent. Here it is simply
   `unique(poll, profile)` — no denormalised flag, no parent to disagree with.
3. **Results must be denormalised counts.** A results page that aggregates votes per render is the
   whale rule's exact shape on the one page everybody opens simultaneously.

## The shape of a poll

Two kinds, chosen by the author and fixed per poll:

| Kind | Options are | Example |
|---|---|---|
| **Text** | Hand-written strings | "Which is worse: a missable trophy, or a 100-hour grind?" |
| **Game** | Concepts from the catalogue, with cover art | "Best soundtrack of 2026" |

They are fundamentally different questions and the plan is to run **one of each at a time**, which
makes the distinction legible rather than confusing. The kind lives on the poll; every option in it
matches.

GOTY-style "categories" are several independent polls sharing a window, not a new grouping concept.
If a real season needs one later it is a nullable FK; building it now would be one abstraction for
one hypothetical event.

### Lifecycle

`draft -> open -> closed -> revealed`, driven by timestamps rather than a state column a cron has to
remember to advance:

- `opens_at` — voting starts. Announced to Discord (see [Reuse](#reuse)).
- `closes_at` — voting stops. Results still hidden.
- `reveals_at` — results and prediction scoring become public.

The gap between close and reveal is deliberate: it is what makes the reveal an event rather than a
running total, and it stops late voters steering toward a visible winner.

## Data model (proposed)

| Model | Shape |
|---|---|
| `Poll` | `question`, `kind` (text/game), `opens_at`, `closes_at`, `reveals_at`, `winning_option` (set at reveal), `vote_count`, `author` |
| `PollOption` | `poll`, `label` **or** `concept` (exactly one, CheckConstraint), `position`, `vote_count`, `prediction_count` |
| `PollVote` | `poll`, `profile`, `option`, `predicted_option`, `created_at`, `unique(poll, profile)` |

Load-bearing choices:

- **`unique(poll, profile)`**, unconditional. The whole integrity story of the feature, in one line.
- **Exactly one of `label` / `concept`**, as a CheckConstraint. Both set or neither is a row nothing
  can render, and the admin, the shell and a data migration all write around any service.
- **`PollVote.option` FKs `PollOption`**, not `Concept`. Deleting an option takes its votes with it,
  which is correct; a vote pointing at the catalogue would outlive the thing it was a vote for.
- **`predicted_option` is NOT NULL.** A vote without a prediction is a different feature, and making
  it optional means every accuracy figure needs a denominator caveat forever.
- **`winning_option` is stored at reveal**, not derived. Prediction accuracy is then a join rather
  than a per-profile recount, which is what keeps a Career stat whale-safe. It also freezes the
  answer: a later vote-count correction cannot retroactively change who predicted correctly.
- **`vote_count` denormalised on both `Poll` and `PollOption`**, written only by the vote service.

### The absorb() obligation

`PollOption.concept` is a ForeignKey to `Concept`, so **`Concept.absorb()` must gain a branch** or a
catalogue merge silently destroys options and the votes attached to them. CLAUDE.md documents this as
a standing rule and the inventory there must be updated in the same change.

The dedup is the `GameListItem` shape, not the harder `PromptGame` one: re-point `concept_id` to the
survivor, excluding any option whose poll already holds the survivor (those cascade). A poll with two
options for the same game is a spoiled poll, so the collision must drop rather than merge — and
because votes hang off the option row, `vote_count` on the poll needs repairing afterwards.

## Reuse (searched, not assumed)

| Need | Use | Where |
|---|---|---|
| Periodic community thing on a schedule | `CommunityTrophyDay` + its management command | `core/models.py:121`, the `post_community_trophy_tracker` command |
| Announcing to Discord | the same webhook path that posts the trophy tracker | ditto |
| Authoring surface | the staff hub, rather than Django admin | `/staff/`, `core/staff_views.py` |
| Engagement analytics | `SiteEvent` (add `poll_vote`, `poll_reveal_view`) | `core/models.py:8` |
| Game picker for a game poll | `PP.GameAdder` + `gamelists.services.game_search` | `static/js/utils.js`, extracted 2026-09-19 |
| Segmented tabs, tallies, carded surfaces | the house primitives | `docs/reference/design-system.md` |

**Genuinely new**: the prediction mechanic and its scoring.

## Open questions

1. **Where does it live in the IA?** The Community hub was retired in 2026-08. Candidates: the home
   lobby (it is a site-wide event), or Career (it accumulates a stat). Needs a decision before URLs.
2. **Does prediction accuracy feed anything?** A Career stat and a Pursuer Card line are the obvious
   homes. Deciding now affects whether accuracy is stored per profile or computed from votes.
3. **Cadence.** Weekly? Fortnightly? This drives whether the reveal is a page, a Discord post, or
   both, and whether old polls get an archive like the Monthly Recap has.
4. **Does a non-member vote?** Everything here argues yes — a community poll with a paywall is not a
   community poll — but it is worth stating rather than assuming.

## Gotchas and Pitfalls

- **Do not let results leak before `reveals_at`.** The counts live on rows any read can reach, so the
  serializer and the template both have to gate on the clock, not just the page that normally shows
  them. This is the `profile_views.py:670` bug class: a flag checked only in the template, bypassed
  by a request that never rendered the parent.
- **A vote is not editable after `closes_at`**, and the check belongs in the service against the
  locked row, not in the view — the same re-assert-after-lock rule the rest of the site follows.
- **Prediction is about the MAJORITY, not about being right.** The copy has to be careful here: you
  are not predicting a fact, you are predicting your community. That is the charm of it.
