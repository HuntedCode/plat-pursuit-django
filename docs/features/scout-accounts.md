# Scout Accounts

Staff-curated PSN accounts synced on a tighter cadence to discover new trophy lists before regular users encounter them. Scouts use the same `PSNManager.profile_refresh()` pipeline as every other profile; the only difference is that `refresh_profiles` checks them first on a per-scout configurable schedule (default 2h) before processing normal tier-based profiles.

## Architecture Overview

PlatPursuit discovers new games when a user syncs their profile and their `trophy_titles` response contains an `np_communication_id` the database hasn't seen before. To reduce the lag between a game's release and its appearance in the database, staff can designate prolific PSN accounts as "scouts." These accounts are refreshed every 1-2 hours (configurable per scout) rather than the standard 6-12 hour tier schedule.

After the initial sync, incremental refreshes are cheap: `_job_profile_refresh` only fetches trophy titles changed since `last_synced`, so a 2-hour refresh for a scout with no new activity costs just 1-2 API calls.

The "Recently Added" browse page surfaces the results of scout discovery to users, showing new base games and DLC trophy packs sorted by database creation date.

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` | `ScoutAccount` model (OneToOne to Profile) |
| `trophies/admin.py` | `ScoutAccountAdmin` with activate/pause/retire/trigger actions |
| `trophies/token_keeper.py` | `games_discovered` counter increment in `_job_profile_refresh` |
| `core/management/commands/refresh_profiles.py` | Existing cron command: scouts queued first, then standard tiers |
| `trophies/views/game_views.py` | `RecentlyAddedView` (browse page) |
| `core/hub_subnav.py` | "Recently Added" sub-nav item in Browse hub |

## Data Model

### ScoutAccount

| Field | Type | Notes |
|-------|------|-------|
| `profile` | OneToOne(Profile) | The PSN account to sync |
| `status` | CharField | `active` / `paused` / `retired` |
| `refresh_frequency_hours` | PositiveSmallIntegerField | Default 2. How often to refresh |
| `games_discovered` | PositiveIntegerField | Auto-incremented when new games are found during this scout's refresh |
| `staff_notes` | TextField | Internal notes |
| `added_by` | FK(CustomUser) | Staff member who created this scout |

Uses `Profile.last_synced` for scheduling (no separate timestamp needed). Indexed on `(status, created_at)`.

### TrophyGroup.created_at

Added to track when DLC trophy groups were first discovered. Used by the "Recently Added" DLC category. Existing rows received the migration timestamp as their default.

## Key Flows

### Scout Refresh Cycle

1. `refresh_profiles` management command runs every 30 minutes (cron)
2. Scouts are processed first: queries `ScoutAccount.objects.filter(status='active')` with `select_related('profile')`
3. For each scout, compares `profile.last_synced` against `now - timedelta(hours=refresh_frequency_hours)`
4. If due, calls `PSNManager.profile_refresh(scout.profile)` (standard incremental refresh), up to `--max-scouts` cap (default 50)
5. Standard tier-based profiles are processed after scouts
6. TokenKeeper processes the refresh job, which only syncs games changed since `last_synced`
7. If new games are created during the first pass, `ScoutAccount.games_discovered` is incremented

### Adding a Scout

1. Staff navigates to Django admin > ScoutAccount > Add
2. Selects or creates a Profile (by raw ID or search)
3. Sets refresh frequency and any notes
4. On first save, if the profile has never synced (`account_id` is null), staff should trigger a manual initial sync via the admin action

## Rate Limit Budget

- PSN hard limit: 300 calls per 15-minute window
- Per incremental refresh: ~6-10 API calls (profile data + changed trophy titles + title stats)
- 50 scouts at 2h cadence, command runs every 30 min: ~12-13 scouts per run = ~80-130 calls
- Safety valve: `--max-scouts` flag (default 50) on `refresh_profiles`

## Gotchas and Pitfalls

- **Initial sync is expensive.** A scout with 2000+ games will use ~1000+ API calls on first sync. Stagger initial syncs for new scouts rather than adding many at once.
- **Scout profiles can also be real users.** If a real user links their PSN account and that account happens to be a scout, both `refresh_profiles` and `refresh_scouts` may queue refreshes. This is harmless since `profile_refresh()` skips profiles already in `syncing` state.
- **The `games_discovered` counter only increments during refreshes.** The initial sync (`_job_sync_trophy_titles`) does not increment it because every game is "new" on first sync.
- **DLC created_at accuracy.** Existing TrophyGroup rows received the migration timestamp. The "Recently Added" DLC view will only show accurate discovery dates for DLC synced after the migration.
