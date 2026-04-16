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

Badges are PP's custom achievement system. Players earn badge tiers by completing sets of games (stages) defined by staff.

### Badge
A badge tier (Bronze/Silver/Gold/Platinum) within a series. Grouped by `series_slug`. Higher tiers inherit properties from `base_badge` (the tier-1 badge in the same series). Types include: series, collection, megamix, developer, user, misc. Misc badges use a `requirements` JSON field instead of stages. Key display fields: `name`, `display_title`, `display_series`, `badge_image`. The `is_live` flag controls public visibility.

Key relationships:
- `base_badge` FK to self (nullable, for tier inheritance)
- `title` FK to `Title` (awarded to user on earn)
- `most_recent_concept` FK to `Concept`
- `funded_by` FK to `Profile` (donor who funded artwork)

### Stage
Defines one "step" in a badge series. Contains the Concepts a player must platinum (or 100%) to complete the stage. `stage_number` 0 marks optional/tangential entries. `required_tiers` limits which badge tiers require this stage.

Key relationships:
- `concepts` M2M to `Concept`
- Grouped by `series_slug` (matches Badge.series_slug)

### UserBadge
Records that a profile has earned a specific badge tier. One row per profile-badge pair.

Key relationships:
- `profile` FK to `Profile`
- `badge` FK to `Badge`

### UserBadgeProgress
Tracks in-progress badge completion for a profile. Stores `completed_concepts` count and `progress_value`.

Key relationships:
- `profile` FK to `Profile`
- `badge` FK to `Badge`

### StageCompletionEvent
Records when a profile completed a specific stage for a specific badge tier. Used for time-series badge analytics. One record per (profile, badge, stage) triple. Automatically created/deleted by `badge_service._record_stage_completions` during badge evaluation.

Key relationships:
- `profile` FK to `Profile`
- `badge` FK to `Badge`
- `stage` FK to `Stage`
- `concept` FK to `Concept` (SET_NULL, nullable: which concept satisfied the stage)

Key fields:
- `completed_at`: effective completion date (max of game completion vs badge creation for retroactive credit)

### ProfileGamification
OneToOne extension of Profile for gamification stats. Stores `total_badge_xp`, per-series XP breakdown (`series_badge_xp` JSON), and total/unique badge counts. Updated via signals when badge progress changes.

Key relationships:
- `profile` OneToOne to `Profile` (primary key)

### StatType
Defines stat categories for the gamification system (e.g., "badge_xp"). Designed for future expansion to additional stats like Power, Luck, Agility.

### StageStatValue
Per-stage stat configuration. Maps a Stage to a StatType with values for each badge tier (bronze_value, silver_value, gold_value, platinum_value).

Key relationships:
- `stage` FK to `Stage`
- `stat_type` FK to `StatType`

### GameFamily
Groups related Concepts across generations or regions without merging them. Each Concept keeps its own comments, ratings, and checklists. A lightweight cross-gen unification layer with a `canonical_name` and `is_verified` flag.

### GameFamilyProposal
Proposed GameFamily grouping awaiting admin review. Contains a confidence score, match signals, and links to a resulting family if approved.

Key relationships:
- `concepts` M2M to `Concept`
- `resulting_family` FK to `GameFamily` (nullable)
- `reviewed_by` FK to `CustomUser`

---

## Challenge Models

Challenges are long-running goals where players work toward completing sets of platinums under specific constraints.

### Challenge
Base challenge model shared across all challenge types (`az`, `calendar`, `genre`). The `challenge_type` field determines which child slot model applies. Tracks progress via `total_items`, `filled_count`, `completed_count`, and `is_complete`. Supports soft deletion.

Key relationships:
- `profile` FK to `Profile`

### AZChallengeSlot
One of 26 letter slots (A-Z) for an A-Z Challenge. Each slot can hold one Game that the player must platinum.

Key relationships:
- `challenge` FK to `Challenge`
- `game` FK to `Game` (nullable, SET_NULL)

### CalendarChallengeDay
One of 365 calendar day slots (Jan 1 through Dec 31, excluding Feb 29). Filled automatically when the player earns a platinum on that calendar day. Tracks `plat_count` for multiple platinums on the same day.

Key relationships:
- `challenge` FK to `Challenge`
- `game` FK to `Game` (nullable, the first game whose platinum filled the day)

### GenreChallengeSlot
One genre slot in a Genre Challenge. Points to a Concept rather than a Game.

Key relationships:
- `challenge` FK to `Challenge`
- `concept` FK to `Concept` (nullable)

### GenreBonusSlot
Bonus game slot for subgenre hunting in a Genre Challenge, with no genre restriction.

Key relationships:
- `challenge` FK to `Challenge`
- `concept` FK to `Concept` (nullable)

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
A user-created guide for a game Concept. Supports draft/published states, soft deletion, and a `selected_game` FK for trophy item resolution. Denormalized `upvote_count`, `progress_save_count`, and `view_count`.

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
Join table linking a Profile to a Title with source tracking (`source_type`: badge or milestone, `source_id`), earned timestamp, and `is_displayed` flag (one active at a time per profile).

Key relationships:
- `profile` FK to `Profile`
- `title` FK to `Title`

### Milestone
An achievement milestone with various criteria types (plat count, discord linked, badge count, calendar progress, subscription months, etc.). Some are premium-only. Has an optional `title` FK to award and optional `discord_role_id`. `required_value` is auto-synced from `criteria_details.target`.

Key relationships:
- `title` FK to `Title` (nullable)

### UserMilestone
Records that a profile has earned a specific milestone.

Key relationships:
- `profile` FK to `Profile`
- `milestone` FK to `Milestone`

### UserMilestoneProgress
Tracks in-progress milestone completion for a profile. Stores `progress_value` toward the milestone's `required_value`.

Key relationships:
- `profile` FK to `Profile`
- `milestone` FK to `Milestone`

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
User-created game collection (e.g., "My Backlog", "Favorites"). Free users: up to 3 private lists, 100 games each. Premium users: unlimited, public visibility, notes. Denormalized `game_count`, `like_count`, `view_count`. Supports soft deletion.

Key relationships:
- `profile` FK to `Profile`

### GameListItem
A game entry within a GameList. Tracks position for custom ordering and optional notes.

Key relationships:
- `game_list` FK to `GameList`
- `game` FK to `Game`

### GameListLike
Like on a public GameList. One per profile per list.

### PublisherBlacklist
Tracks publishers flagged for shovelware. When any concept from a publisher is flagged, the entire publisher's catalog gets flagged. Stores `flagged_concepts` as a JSON list of concept IDs.

### APIAuditLog
Logs PSN API calls for token monitoring: endpoint, status code, response time, remaining quota.

---

## User & Account Models (users app)

### CustomUser
Extends Django's AbstractUser with email-based authentication. Key fields: `stripe_customer_id`, `paypal_subscription_id`, `subscription_provider` (stripe/paypal), `premium_tier`, `user_timezone`, `default_region`, `email_preferences` (JSON). The `is_premium()` method checks subscription status against both Stripe and PayPal.

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

### PlatinumShareImage
Generated share images for platinum notifications. Supports landscape and portrait formats. Stored in S3.

Key relationships:
- `notification` FK to `Notification`

### ScheduledNotification
Scheduled notifications for future delivery. Targeting supports: all users, premium tiers, discord verified, or individual users. Processed hourly by a management command.

Key relationships:
- `created_by` FK to `CustomUser`

### NotificationLog
Audit log for bulk notification sends. Snapshots the sent content and tracks recipient count.

### DeviceToken
Push notification tokens for mobile devices (iOS/Android). Used by the FCM push notification service.

Key relationships:
- `user` FK to `CustomUser`

---

## Core/Infrastructure Models (core app)

### SiteSettings
Singleton model (id=1) for site-wide settings. Currently stores `index_page_view_count` and `session_tracking_enabled_at`.

### PageView
Deduplicated page view records. One row per unique session+page per 30-minute window. Tracks `page_type`, `object_id`, viewer identity, and analytics session.

### SiteEvent
Internal event tracking for admin analytics. Event types include guide visits, share card downloads, recap interactions, game list actions, challenge events, easter eggs, and sync searches.

### AnalyticsSession
Analytics session with 30-minute inactivity timeout. Tracks page sequence, referrer, and user agent. Separate from Django sessions.

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
  |-- 1:N --> DeviceToken
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
  |-- 1:N --> UserMilestone
  |-- 1:N --> UserMilestoneProgress
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
  |-- M2M <-> GameFamilyProposal (via proposal.concepts)

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
  |-- 1:N --> PlatinumShareImage
```

### Key Design Patterns

- **Denormalized counters**: Most entities store pre-computed counts (`earned_count`, `upvote_count`, `view_count`) updated via signals or service methods, avoiding expensive COUNT queries at read time.
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
