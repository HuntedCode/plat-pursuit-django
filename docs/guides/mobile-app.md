# Mobile App

> **STATUS: removed 2026-08. There is no mobile API.** This page is the record of what existed, why it
> went, and what to know when a mobile client is actually built.

## What was here

A backend-only foundation for a companion React Native (Expo) app. Phases 1-2 of a seven-phase plan had
shipped: CORS, token auth, and 15 endpoints under `/api/v1/auth/` and `/api/v1/mobile/`, plus a
`DeviceToken` model for push registration. Phases 3-7 (push infrastructure, the Expo app itself, screens,
polish) were never started.

Deleted in the badge cutover (step 5b.3), in two passes:

| Removed | Why |
|---|---|
| `api/mobile_badge_views.py` + 4 routes | Tier-shaped throughout (`tier`, `all_tiers`, `user_highest_tier`). The grouping-badge subsystem has no tiers; badges are per-EDITION. Rewriting it would have meant inventing a response shape for zero clients. |
| `mobile_auth_views` / `mobile_auth_urls`, `mobile_profile_views`, `mobile_psn_views`, `mobile_sync_views`, `mobile_game_views`, `device_token_views` + 11 routes | Same reasoning applied to the rest: a client will be a full rebuild whenever it happens, so the API it talks to should be designed against that client, not inherited from a two-year-old plan. |
| `notifications.DeviceToken` (model + admin + table, migration `0017_drop_device_token`) | Fully dead. Its only writer was the mobile logout view, nothing ever read it, and the `PushNotificationService` its docstring named was never written. |

Verified unconsumed before deletion: PlatBot calls only the bot endpoints (`/verify/`, `/sync-roles/`,
`/recheck-badges/`, `/refresh/`), and no PlatPursuit mobile client exists in any state.

## What survived, and why

- **DRF token auth** (`rest_framework.authtoken`, `TokenAuthentication` as a default authentication class).
  PlatBot depends on it: `IsDiscordBot` authorises by matching the token key against `BOT_API_KEY`. This is
  not mobile scaffolding and must not be removed with it.
- **`django-cors-headers` + `CORS_ALLOWED_ORIGINS`.** Inert unless the env var is set, and a mobile
  rebuild wants it back.
- **`api/serializers.py`** (`ProfileSerializer`, `GenerateCodeSerializer`). Shared with the bot-facing
  `api/views.py`, so these were never mobile-only.

## When rebuilding

- **Design the API against the client, not against the web views.** The deleted endpoints were shaped by
  guesses about screens that were never designed, which is most of why they aged into nothing.
- **Badges are `BadgeSeries` x `PlatformGroup` -> `GroupBadge`, earned per edition.** Read holds from
  `UserGroupBadge` and standings from `ProfileBadgeStanding` / `SeriesBadgeStanding` /
  `ProfileEditionStanding`. See [badge-backend-rebuild.md](../design/rebuild/badge-backend-rebuild.md).
- **Whale safety applies identically.** A mobile profile endpoint hitting a 250,000-trophy account has the
  same failure mode as the web one; see the aggregation rule in the project CLAUDE.md.
- **Push needs a real send path**, not just a registration table. That was the gap last time: `DeviceToken`
  collected tokens for a service that did not exist.

The original seven-phase plan is at `C:\Users\Jlowe\.claude\plans\floating-rolling-teapot.md`. Treat it as
history: it predates the hub IA, the rebuild, and the badge cutover.
