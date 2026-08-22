# Data Model

Platinum Pursuit's data model is organized across five Django apps: **trophies** (the core domain: profiles, games, trophies, badges, challenges, checklists, comments, reviews), **users** (authentication and subscriptions), **notifications** (in-app and push notifications), **core** (site-wide infrastructure: analytics, email logging, settings), and **fundraiser** (donation campaigns). The central axis of the system is the **Profile > Game > Trophy** chain sourced from PSN, with **Concept** acting as the cross-stack unifier that ties together regional/platform variants of the same game. Nearly all user-facing features (badges, ratings, comments, checklists, challenges) hang off either Profile or Concept.

---

## Core Trophy Hunting Models

These models form the foundation of the platform. They represent PSN data synchronized from the PlayStation API and the relationships between players, games, and trophies.

### Profile
The PSN player identity on the platform. Links to a `CustomUser` via a OneToOne FK (nullable, since profiles can exist before a user registers). Stores denormalized trophy totals (`total_trophies`, `total_plats`, `total_completes`, `avg_progress`), sync state (`sync_status`, `sync_progress_value`, `sync_progress_target`), and display preferences (`selected_background` FK to Concept, `selected_theme`, `hide_hiddens`). Also tracks Discord integration (`discord_id`, `is_discord_verified`) and verification state.

Key relationships:
- `user` OneToOne to `CustomUser` (nullable, SET_NULL)
- `recent_plat` / `rarest_plat` FK to `EarnedTrophy`
- `selected_background` FK to `Concept`

### Game
A single PSN trophy list (one per `np_communication_id`). Different regional stacks of the same game are separate Game rows. Key fields include `title_name`, `title_platform` (JSON list of platforms), `title_image`, `region` (JSON list), `title_ids` (JSON list), `defined_trophies` (JSON dict of counts by type), and moderation flags (`shovelware_status`, `is_obtainable`, `is_delisted`, `has_online_trophies`). The `concept_lock` flag prevents sync from overwriting an admin-assigned concept. The `lock_title` flag prevents sync from overwriting an admin-cleaned title.

**Title cleaning:** All title/name fields are automatically cleaned on `save()` via model overrides. `clean_title_field()` strips trademark symbols and normalizes Unicode Roman numerals. Game titles additionally use `clean_game_title()`, which also strips PSN suffixes like "Trophy Set" and "Trophies". The `clean_titles` management command runs these same functions in bulk for retroactive cleanup. Both functions live in `trophies/models.py`.

Key relationships:
- `concept` FK to `Concept` (nullable, SET_NULL)

### Concept
The cross-stack unifier. All regional/platform variants of the same game share one Concept. Stores publisher info, release date, genres, media URLs, and a `slug` for URL routing. The `absorb(other)` method is critical: it migrates all related data (comments, ratings, checklists, featured guides, badge references, stages, challenge slots, etc.) from one Concept to another when a game is reassigned. Any new model with a relationship to Concept **must** update `absorb()`.

Key relationships:
- `family` FK to `GameFamily` (nullable)
- Reverse: `games` (Game.concept), `comments`, `checklists`, `reviews`, `user_ratings`, `concept_trophy_groups`, `featured_entries`, `stages` (M2M via Stage)

### Trophy
An individual trophy within a Game. Identified by `trophy_id` (integer, positional within the game) and linked to its Game. Tracks both PSN-global rarity (`trophy_earn_rate`) and platform-specific rarity (`earn_rate`). `earned_count` is the denormalized count of PP users who have earned it.

Key relationships:
- `game` FK to `Game`
- `earned_by` M2M to `Profile` through `EarnedTrophy`

### EarnedTrophy
The through table for Trophy-to-Profile. Tracks whether a specific trophy has been earned by a specific profile, along with earn date, progress data, and a `user_hidden` flag for deleted/hidden games.

Key relationships:
- `profile` FK to `Profile`
- `trophy` FK to `Trophy`

### TrophyGroup
Represents a DLC group within a Game (e.g., "Base Game", "DLC Pack 1"). Identified by `trophy_group_id` ("default" for base game, "001"/"002" for DLC). Stores its own `defined_trophies` count summary.

Key relationships:
- `game` FK to `Game`

### ConceptTrophyGroup
Concept-level abstraction of trophy groups. Unifies DLC groups across game stacks of the same Concept. Used as the scope for ratings and reviews (users rate the base game or a specific DLC separately).

Key relationships:
- `concept` FK to `Concept`

### TitleID
Mapping record for PSN Title IDs. Each record stores a `title_id`, its `platform`, and `region`. Used during sync to resolve game identity.

### ProfileGame
The join between Profile and Game. Tracks play statistics: `progress` (0-100), `earned_trophies` (JSON dict), `first_played_date_time`, `last_played_date_time`, `has_plat`, `hidden_flag`, and denormalized trophy counts.

Key relationships:
- `profile` FK to `Profile`
- `game` FK to `Game`
- Unique together on (profile, game)

### FeaturedProfile
Admin-managed featured profile display with priority and date range.

### FeaturedGame
Admin-managed featured game display with priority and date range.

### FeaturedGuide
Links a Concept to a featured period with priority for homepage display.

Key relationships:
- `concept` FK to `Concept`

---

## Badge & Gamification Models

Badges are PP's custom achievement system. Staff curate a **series** of games (stages); a hunter earns the
badge by completing them.

> **Rebuilt 2026-08.** A badge is now **series x platform edition**, not one of four tiers. The tier
> models below (`Badge`, `UserBadge`, `UserBadgeProgress`, `StageCompletionEvent`, `ProfileGamification`)
> are RETAINED for rollback and audit but have **no writer anywhere in the codebase**. See
> [badge-system.md](badge-system.md) for how the live engine works.

### The live models

#### PlatformGroup
A platform set that defines an **edition**, e.g. Ultra HD (PS4/PS5) or Legacy HD (PS3/Vita). Carries the
shared visual identity every medallion of that edition draws with (`medallion_shape`, `backing_key`,
`background_image`) plus the routing predicate `matches_platforms()`.

Key fields: `key` (unique), `name`, `platforms` (ArrayField of PSN `title_platform` values),
`exclude_delisted` (Ultra HD counts delisting; Legacy HD does not), `sort_order`, `is_active`.

#### BadgeSeries
The abstract series-level definition, one row per `series_slug`. Holds the shared identity and the
DEFAULTS each edition inherits. **No `is_live` and no `tier`** -- liveness is per-edition.

Key fields: `series_slug` (unique), `name`, `badge_type`, `completion_policy` (`all` / `min_count`),
`min_required`, `description`, `display_series`, `badge_image`, `holo_badge_image`.

Key relationships:
- `franchise` / `collection` FK to `Franchise`, `developer` FK to `Company` (drives the coverage audit)
- `submitted_by` FK to `Profile` (credited on `user` badges; their avatar is the art fallback)
- `funded_by` FK to `Profile` (donor credited for the artwork -- what the fundraiser writes)
- `title` FK to `Title` (granted on earning ANY edition of the series)

#### GroupBadge
**The earnable thing**: one row per (`BadgeSeries` x `PlatformGroup`), `unique_together`.

Key fields: `is_live` (default False -- hidden until released), `set_number`, and the engine-owned denorms
`earned_count`, `required_stages`, `rarity_pct`, `rarity_rank`, `rarity_class`.

Per-edition overrides, each null-means-inherit: `badge_image_override`, `holo_badge_image_override`,
`funded_by_override`. Resolved by `effective_funded_by`, `effective_holo_image`, and `art_layers()` --
which is the single source of truth for medallion composition, because backdrop and shape come from the
`PlatformGroup` and a series alone cannot draw itself.

#### UserGroupBadge
A profile's CURRENT hold. **Binary**: the row exists iff the profile meets the bar, and a revoke DELETES
it. There is no `maintenance` state and no permanent rank -- rank is the live position among current
holders ordered by `earned_at`.

Key fields: `earned_at` (the hunter's COMPLETION date, resynced when a badge's iteration changes),
`created_at` (when WE awarded the row -- use this for "earned this week"), `is_holo`, `is_displayed`.

#### GroupBadgeAnnouncement
Append-only marker: one row per (profile, group_badge) ever announced to Discord. Never deleted. Exists
because holds are binary, so a revoke-then-re-earn is indistinguishable from a first earn and would
re-ping a hunter about a badge they have held for a year.

#### ProfileBadgeStanding / SeriesBadgeStanding / SeriesEditionStanding / ProfileEditionStanding
The materialized read-models the leaderboards sort on, all recomputed from scratch by
`badge_xp.recompute_standing` on every evaluation (so they cannot drift):

| model | grain | holds |
|---|---|---|
| `ProfileBadgeStanding` | per profile | grand `total_xp`, `badges_held`, `country_code` |
| `SeriesBadgeStanding` | per (profile, series) | `xp`, `progress_bp`, `stages_cleared`/`total`, `advanced_at`, per-edition `group_progress` |
| `SeriesEditionStanding` | per (profile, series, STARTED edition) | that edition's `xp`, `stages_cleared`/`gating_count`, and its OWN `advanced_at` |
| `ProfileEditionStanding` | per (profile, edition) | the same totals pre-sliced, backing the boards' edition filter |

`SeriesEditionStanding` is the one with a membership rule: a row exists only for an edition the hunter has
STARTED, because it backs a board. `SeriesBadgeStanding.group_progress` deliberately keeps untouched
editions so the Collection wall has a denominator, so the two are not redundant despite overlapping. Its
`advanced_at` is the reason it is a table rather than a JSON key -- its parent's is series-wide, and
tiebreaking one edition's board on another edition's date meant advancing in one could drop a rank in the
other.

#### Stage
One "step" in a series: the Concepts a hunter must complete. `stage_number` 0 marks optional entries.

**Joined to a series by the `series_slug` STRING, not an FK** -- so nothing stops a stage existing under a
slug no series owns, and renaming a slug orphans its stages.

Key relationships:
- `concepts` M2M to `Concept`
- `concept_bundles` reverse FK to `ConceptBundle` (a concept is in EITHER `concepts` OR a bundle on that
  stage, never both -- any "which series is this game in" query must check both paths)

### Retained tier models (no writer)

#### Badge
A tier (Bronze/Silver/Gold/Platinum) within a series, grouped by `series_slug`, with higher tiers
inheriting from `base_badge`. Superseded by `BadgeSeries` + `GroupBadge`. No admin registration since the
2026-08 fundraiser/art_reveal repoint.

#### UserBadge / UserBadgeProgress
Earn records and in-progress tracking for the tier engine. Superseded by `UserGroupBadge` (holds) and the
standing tables (progress).

#### StageCompletionEvent
Per-(profile, badge, stage) completion events for time-series analytics. The current engine records none.

#### ProfileGamification
OneToOne on Profile holding `total_badge_xp` and a per-series JSON breakdown. **Frozen**: it counts the
retired tier economy and is NOT comparable to `ProfileBadgeStanding.total_xp`. Never show them as the
same figure.

### StatType
Defines stat categories for the gamification system. **Vestigial**: the P.L.A.T.I.N.U.M. 8-stat system it
was built for was retired in 2026-06; only the `badge_xp` record exists.

### StageStatValue
Per-stage stat configuration, per badge tier. **Vestigial** for the same reason. The discipline radar
derives from job levels (`ProfileJobXP`), not this table.

### GameFamily
Groups related Concepts across platforms and regions without merging them.
Each Concept keeps its own comments, ratings, and checklists. Keyed on
`igdb_id` (unique, nullable) — one family per IGDB game. Populated
automatically by the IGDB enrichment pipeline via `get_or_create`; the
old heuristic matcher and `GameFamilyProposal` review queue were removed
in Phase 2.6. See [Game Family System](../features/game-family.md) for
the full model and flow.

---

## Challenge Models

**RETIRED 2026-08.** The `Challenge`, `AZChallengeSlot`, `CalendarChallengeDay`, `GenreChallengeSlot`,
and `GenreBonusSlot` models were dropped in migration `0281_drop_challenge_system`. Challenges will be
rewritten from scratch; see [challenge-systems](../features/challenge-systems.md) for the design reference.

### ArchivedAZChallenge
Frozen A-Z challenge progress, preserved when the Challenge system was retired. One row per archived
A-Z challenge, keyed on stable PSN ids (`psn_username` + per-slot `np_communication_id` inside the
`slots` JSON) so a rebuilt system can re-import it. Read-only historical data, not wired into any live
feature. Calendar and Genre progress were deliberately not preserved.

Key relationships:
- `profile` FK to `Profile` (nullable, SET_NULL)

---

## Community Models

User-generated content for game discussion, ratings, and reviews.

### Comment
Threaded comment system unified across game stacks via Concept. Scoped to concept-level (`trophy_id` null, `checklist_id` null), trophy-level (`trophy_id` set), or checklist-level (`checklist_id` set). Supports self-referential threading via `parent` FK. Denormalized `depth` and `upvote_count`. Supports soft delete with moderation logging.

Key relationships:
- `concept` FK to `Concept`
- `profile` FK to `Profile`
- `parent` FK to self (nullable, for threading)

### CommentVote
Upvote on a Comment. One per profile per comment.

### CommentReport
User-filed report on a Comment for moderation review. Tracks status (pending/reviewed/dismissed/action_taken).

### UserConceptRating
Numeric ratings for a game: difficulty (1-10), grindiness (1-10), hours_to_platinum, fun_ranking (1-10), overall_rating (0.5-5.0). Scoped to a ConceptTrophyGroup so base game and DLC can be rated separately.

Key relationships:
- `profile` FK to `Profile`
- `concept` FK to `Concept`
- `concept_trophy_group` FK to `ConceptTrophyGroup` (nullable: null means base game)

### Review
Text-based game review with thumbs up/down recommendation and markdown body. Scoped to a ConceptTrophyGroup. Denormalized vote counts (`helpful_count`, `funny_count`) and `reply_count`. Supports soft delete with moderation logging.

Key relationships:
- `concept` FK to `Concept`
- `concept_trophy_group` FK to `ConceptTrophyGroup`
- `profile` FK to `Profile`
- Unique together on (profile, concept, concept_trophy_group)

### ReviewVote
Helpful or Funny vote on a Review. One vote per type per profile per review.

### ReviewReply
Single-level (flat, not nested) reply to a Review. Supports soft delete.

### ReviewReport
User-filed report on a Review for moderation review.

### ModerationLog
Audit trail for comment moderation actions. Preserves original comment body, author, and context for accountability. Uses PROTECT on moderator FK to prevent history deletion.

### ReviewModerationLog
Audit trail for review moderation actions. Same pattern as ModerationLog.

### BannedWord
Staff-managed list of banned words for automatic comment filtering. Supports whole-word-only matching via `use_word_boundaries`.

---

## Checklist Models

User-created trophy guides with trackable progress.

### Checklist
A user-created guide for a game Concept. Supports draft/published states, soft deletion, and a `selected_game` FK for trophy item resolution. Denormalized `upvote_count` and `progress_save_count`.

Key relationships:
- `concept` FK to `Concept`
- `selected_game` FK to `Game` (nullable)
- `profile` FK to `Profile`

### ChecklistSection
A section within a Checklist. Contains a subtitle, optional description, optional thumbnail, and an `order` field for positioning.

Key relationships:
- `checklist` FK to `Checklist`

### ChecklistItem
An individual entry within a ChecklistSection. Types: item (checkable), sub_header (visual separator), image (premium, with uploaded image), text_area (long text block), trophy (linked to a specific trophy_id). Ordered within section.

Key relationships:
- `section` FK to `ChecklistSection`

### ChecklistVote
Upvote on a Checklist. One per profile per checklist.

### UserChecklistProgress
Tracks a user's progress on a checklist. Stores completed item IDs as a JSON list and denormalized progress stats. Premium users can track any checklist; free users only their own.

Key relationships:
- `profile` FK to `Profile`
- `checklist` FK to `Checklist`

### ChecklistReport
User-filed report on a Checklist for moderation review.

### ChecklistItemVote
_(Not present in the model file as a separate model; checklist voting uses ChecklistVote at the checklist level.)_

---

## Content & Display Models

### Title
A cosmetic display title that appears on a user's profile (e.g., "Platinum Hunter"). Created once, shared across all earners.

### UserTitle
Join table linking a Profile to a Title with source tracking (`source_type`: badge, badge_series, or milestone -- the last being historical one-off awards from the retired legacy engine; `source_id`), earned timestamp, and `is_displayed` flag (one active at a time per profile).

Key relationships:
- `profile` FK to `Profile`
- `title` FK to `Title`

**Milestone / UserMilestone / UserMilestoneProgress: RETIRED 2026-08.** The legacy milestone engine
was dropped in migration `0282_drop_legacy_milestone_engine`. Milestones now live in the dedicated
`milestones` app (`Milestone`, `MilestoneTier`, `EarnedMilestoneTier`, `UserMilestone` there); see
[milestones-revamp](../design/milestones-revamp.md). The titles the legacy metric ladders granted were
deleted with it; its one-off manual awards survive as `UserTitle` rows with `source_type='milestone'`
and a now-dangling `source_id` (a plain integer, not an FK).

### UserTrophySelection
Up to 10 hand-picked "showcase" trophies per profile. Enforces the 10-item limit at the model level.

Key relationships:
- `profile` FK to `Profile`
- `earned_trophy` FK to `EarnedTrophy`

### MonthlyRecap
Pre-computed monthly trophy recap for a profile. Stores trophy aggregates, platinum details, activity calendar, streak data, time analysis, quiz data, badge stats, and comparison data as JSON fields. Becomes immutable once `is_finalized` is True. Tracks email and notification send status.

Key relationships:
- `profile` FK to `Profile`
- Unique together on (profile, year, month)

### GameList
User-created game collection (e.g., "My Backlog", "Favorites"). Free users: up to 3 private lists, 100 games each. Premium users: unlimited, public visibility, notes. Denormalized `game_count` and `like_count`. Supports soft deletion.

Key relationships:
- `profile` FK to `Profile`

### GameListItem
A game entry within a GameList. Tracks position for custom ordering and optional notes.

Key relationships:
- `game_list` FK to `GameList`
- `game` FK to `Game`

### GameListLike
Like on a public GameList. One per profile per list.

### DeveloperReputation
Tracks the shovelware reputation of IGDB developers. Keyed by `Company` (OneToOne). A concept's shovelware standing keys off its **median** platinum earn rate across versions (robust to a single inflated low-population version). `is_blacklisted` is True when **more than 50%** of the company's platinum-bearing, primary-developed concepts are independently shovelware (median >= threshold, 3-concept floor); when a concept's primary developer is blacklisted, the whole concept is auto-flagged unless its median is below the shield threshold (40%). `is_whitelisted` is an admin full exemption (wins over blacklist): a whitelisted developer's primary-developed concepts are never auto-flagged. The blacklist proportion is **derived live** via `qualifying_concepts_for(company)` (numerator, median computed in Postgres with `percentile_cont`) and `primary_developed_concepts(company)` (denominator); only `is_whitelisted` is stored. Hysteresis lives in the numerator rate: enter at the 80% bar, leave when the proportion drops to <= 50% at the 70% bar. See [docs/reference/shovelware-detection.md](../reference/shovelware-detection.md).

### APIAuditLog
Logs PSN API calls for token monitoring: endpoint, status code, response time, remaining quota.

---

## User & Account Models (users app)

### CustomUser
Extends Django's AbstractUser with email-based authentication. Key fields: `stripe_customer_id`, `paypal_subscription_id`, `subscription_provider` (stripe/paypal), `premium_tier`, `user_timezone`, `email_preferences` (JSON). (`default_region` was deleted 2026-08: zero consumers.) The `is_premium()` method checks subscription status against both Stripe and PayPal.

Key relationships:
- Reverse: `profile` OneToOne from Profile

### SubscriptionPeriod
Tracks individual subscription periods for loyalty milestone calculations. A new period is created on activation and closed (`ended_at` set) on deactivation. Enforces one open period per user via a DB constraint.

Key relationships:
- `user` FK to `CustomUser`

---

## Notification Models (notifications app)

### NotificationTemplate
Reusable notification templates with `{variable}` placeholders for title, message, and action URL. Can be auto-triggered by events or used manually. Types include: platinum_earned, badge_awarded, milestone_achieved, monthly_recap, subscription events, payment warnings, and admin announcements.

### Notification
An individual rendered notification for a user. Stores the substituted title, message, detail (markdown), icon, action URL, optional banner image, structured sections (JSON), priority, and metadata. Tracks read status.

Key relationships:
- `recipient` FK to `CustomUser`
- `template` FK to `NotificationTemplate` (nullable)

### ScheduledNotification
Scheduled notifications for future delivery. Targeting supports: all users, premium tiers, discord verified, or individual users. Processed hourly by a management command.

Key relationships:
- `created_by` FK to `CustomUser`

### NotificationLog
Audit log for bulk notification sends. Snapshots the sent content and tracks recipient count.

---

## Core/Infrastructure Models (core app)

### SiteEvent
Internal event tracking for admin analytics. Event types include guide visits, share card downloads, recap interactions, game list actions, challenge events, easter eggs, and sync searches.

> **Removed 2026-08**: `AnalyticsSession`, `PageView` and `SiteSettings` were deleted along with the
> per-request page-view tracking system. Its background threads leaked Postgres connections under
> scraper load and its per-response cookie disabled Cloudflare edge caching site-wide. Traffic
> analytics now come from Cloudflare and Search Console. `SiteEvent` (deliberate user actions) stayed.

### EmailLog
Audit trail for all emails sent from the platform. Tracks email type (subscription lifecycle, account, content, fundraiser), status (sent/suppressed/failed), and trigger source.

Key relationships:
- `user` FK to `CustomUser` (nullable)

---

## Fundraiser Models (fundraiser app)

### Fundraiser
A fundraising campaign with dates, description, minimum donation, and banner configuration. Campaign type determines reward mechanics (currently `badge_artwork` only). Lifecycle methods: `is_upcoming()`, `is_live()`, `is_ended()`.

### Donation
A one-time donation to a Fundraiser. Tracks amount, payment provider (Stripe/PayPal), transaction ID, status, and `badge_picks_earned` (floor of amount/10 for badge artwork campaigns).

Key relationships:
- `fundraiser` FK to `Fundraiser`
- `user` FK to `CustomUser` (nullable)
- `profile` FK to `Profile` (denormalized for donor wall)

### DonationBadgeClaim
A donor's claim on a specific badge series for artwork commissioning. The `badge` field is a OneToOneField to enforce that each badge can only be claimed by one donor (DB-level constraint). Tracks status: claimed, in_progress, completed.

Key relationships:
- `donation` FK to `Donation`
- `profile` FK to `Profile`
- `badge` OneToOne to `Badge`

---

## Dashboard Models

### DashboardConfig
Per-user dashboard preferences. OneToOne extension of Profile (primary key). Stores `module_order` (JSON list of slugs), `hidden_modules` (JSON list), and `module_settings` (JSON dict for per-module overrides).

Key relationships:
- `profile` OneToOne to `Profile` (primary key)

---

## Entity Relationship Summary

The diagram below shows the major entities and their primary connections. FK arrows point from child to parent. M2M relationships are noted separately.

```
CustomUser
  |-- 1:1 --> Profile
  |-- 1:N --> SubscriptionPeriod
  |-- 1:N --> Notification (as recipient)
  |-- 1:N --> EmailLog
  |-- 1:N --> Donation

Profile
  |-- 1:1 --> ProfileGamification
  |-- 1:1 --> DashboardConfig
  |-- N:1 --> Concept (selected_background)
  |-- N:1 --> EarnedTrophy (recent_plat, rarest_plat)
  |-- 1:N --> ProfileGame
  |-- 1:N --> EarnedTrophy
  |-- 1:N --> UserBadge
  |-- 1:N --> UserBadgeProgress
  |-- 1:N --> UserTitle
  |-- 1:N --> UserTrophySelection
  |-- 1:N --> Comment
  |-- 1:N --> Review
  |-- 1:N --> Checklist
  |-- 1:N --> Challenge
  |-- 1:N --> MonthlyRecap
  |-- 1:N --> GameList
  |-- 1:N --> UserConceptRating
  |-- 1:N --> UserChecklistProgress
  |-- 1:N --> Donation
  |-- 1:N --> DonationBadgeClaim

Concept
  |-- N:1 --> GameFamily
  |-- 1:N --> Game
  |-- 1:N --> Comment
  |-- 1:N --> Review
  |-- 1:N --> Checklist
  |-- 1:N --> UserConceptRating
  |-- 1:N --> ConceptTrophyGroup
  |-- 1:N --> FeaturedGuide
  |-- M2M <-> Stage (via Stage.concepts)

Game
  |-- N:1 --> Concept
  |-- 1:N --> Trophy
  |-- 1:N --> TrophyGroup
  |-- 1:N --> ProfileGame
  |-- 1:N --> AZChallengeSlot
  |-- 1:N --> CalendarChallengeDay
  |-- 1:N --> GameListItem

Trophy
  |-- N:1 --> Game
  |-- M2M <-> Profile (through EarnedTrophy)
  |-- 1:N --> EarnedTrophy

Badge
  |-- N:1 --> Badge (base_badge, self-reference)
  |-- N:1 --> Title
  |-- N:1 --> Concept (most_recent_concept)
  |-- N:1 --> Profile (funded_by)
  |-- 1:N --> UserBadge
  |-- 1:N --> UserBadgeProgress
  |-- 1:1 <-- DonationBadgeClaim (artwork_claim)

Stage (linked to Badge via series_slug, not FK)
  |-- M2M --> Concept
  |-- 1:N --> StageStatValue

Challenge
  |-- N:1 --> Profile
  |-- 1:N --> AZChallengeSlot
  |-- 1:N --> CalendarChallengeDay
  |-- 1:N --> GenreChallengeSlot
  |-- 1:N --> GenreBonusSlot

Checklist
  |-- N:1 --> Concept
  |-- N:1 --> Profile
  |-- 1:N --> ChecklistSection --> 1:N --> ChecklistItem
  |-- 1:N --> ChecklistVote
  |-- 1:N --> UserChecklistProgress
  |-- 1:N --> ChecklistReport

Review
  |-- N:1 --> Concept
  |-- N:1 --> ConceptTrophyGroup
  |-- N:1 --> Profile
  |-- 1:N --> ReviewVote
  |-- 1:N --> ReviewReply
  |-- 1:N --> ReviewReport

Comment
  |-- N:1 --> Concept
  |-- N:1 --> Profile
  |-- N:1 --> Comment (parent, self-reference)
  |-- 1:N --> CommentVote
  |-- 1:N --> CommentReport

Fundraiser
  |-- 1:N --> Donation --> 1:N --> DonationBadgeClaim

Notification
  |-- N:1 --> CustomUser (recipient)
  |-- N:1 --> NotificationTemplate
```

### Key Design Patterns

- **Denormalized counters**: Most entities store pre-computed counts (`earned_count`, `upvote_count`, `like_count`) updated via signals or service methods, avoiding expensive COUNT queries at read time.
- **Soft delete**: Comments, Reviews, Checklists, GameLists, and Challenges use `is_deleted` + `deleted_at` fields rather than hard deletion, preserving thread structure and audit trails.
- **Concept as unifier**: Regional/platform stacks are separate Game rows, but all user-facing content (comments, ratings, reviews, checklists) is attached to the shared Concept.
- **Stage-Badge linkage**: Stages connect to Badges via `series_slug` (a string match) rather than a direct FK, allowing flexible tier-based stage filtering via `required_tiers`.
- **JSON flexibility**: Fields like `defined_trophies`, `earned_trophies`, `title_platform`, `region`, `title_ids`, `metadata`, and recap data use JSONField for schema-flexible storage.
- **Concept.absorb()**: The critical migration method that must be updated whenever a new model references Concept. See CLAUDE.md for the full list of currently handled relationships.

---

## Related Docs

- `CLAUDE.md`: Project instructions including the Concept.absorb() contract, responsive design standards, and quality workflow
- `docs/architecture/igdb-integration.md`: IGDB-layer models (Company, ConceptCompany, Franchise, ConceptFranchise, IGDBMatch) are documented there rather than here, since they're a supplementary enrichment layer on top of the core PSN-sourced model chain
- `docs/features/review-hub.md`: Community reviews and ratings hub design (formerly `community-hub.md`; renamed because the new `community-hub.md` describes the site-wide Community Hub destination)
- `docs/features/franchise-system.md`: Franchise and collection browse + detail pages
- `docs/dashboard.md`: Dashboard module system design
- `docs/features/`: Feature-specific design documents
- `docs/minigames/`: Mini-game system ("The Arcade") documentation
