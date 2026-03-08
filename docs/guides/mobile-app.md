# Mobile App

Companion React Native app (Expo managed workflow) for PlatPursuit. View-first browsing with push notifications. Currently backend-only: Phases 1-2 (API endpoints) are complete, Phases 3-7 (push infrastructure, Expo app, screens, polish) are pending.

## Current Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Backend foundation (CORS, auth endpoints, DeviceToken model, PSN/sync endpoints) | **Done** |
| 2 | Missing API endpoints (badges, games/trophies) | **Done** |
| 3 | Push infrastructure (Firebase Admin SDK, PushNotificationService, Celery task) | Pending |
| 4 | Expo app scaffold (auth flow, navigation, API client, Zustand store) | Pending |
| 5 | Core screens (Home, Trophies, Notifications, Game Detail, Profile) | Pending |
| 6 | Feature screens (Badges, Challenges, Settings, PSN linking flow) | Pending |
| 7 | Polish + monetization (AdMob, deep linking, EAS Build) | Pending |

**Next step**: Phase 3 requires a Firebase project + service account JSON before end-to-end testing.

**Full plan + handoff**: `C:\Users\Jlowe\.claude\plans\floating-rolling-teapot.md`

## Architecture Overview

The mobile app uses **token-based authentication** (Django REST Framework `TokenAuthentication`) instead of the web app's session-based auth. All mobile endpoints live under `/api/v1/` and return JSON. The web app's existing API endpoints are not reused because they assume session auth and return HTML in some cases.

CORS is configured via `django-cors-headers` with `CORS_ALLOWED_ORIGINS` env var for the Expo dev server.

### Authentication Flow

1. User signs up or logs in via `/api/v1/auth/login/` or `/api/v1/auth/signup/`
2. Server returns a DRF Token
3. All subsequent requests include `Authorization: Token <token>` header
4. Logout invalidates the token via `/api/v1/auth/logout/`

### Push Notifications (Phase 3, Planned)

1. App registers with Firebase Cloud Messaging (FCM) on startup
2. Sends device token to `/api/v1/device-tokens/` (stored in `DeviceToken` model)
3. `PushNotificationService` sends push via Firebase Admin SDK when notifications are created
4. Device tokens cleaned up on logout via DELETE endpoint

## File Map

### New Files (Backend)

| File | Purpose |
|------|---------|
| `api/mobile_auth_views.py` | Login, signup, logout, password reset |
| `api/mobile_auth_urls.py` | URL patterns for auth endpoints |
| `api/mobile_profile_views.py` | Own profile + any public profile by PSN username |
| `api/mobile_psn_views.py` | Generate verification code, verify, link status |
| `api/mobile_sync_views.py` | Sync status + trigger sync |
| `api/mobile_badge_views.py` | Badge series list, detail, user badges, profile badges |
| `api/mobile_game_views.py` | Profile game list, game trophy list |
| `api/device_token_views.py` | Register/unregister push notification device tokens |

### Modified Files

| File | Change |
|------|--------|
| `plat_pursuit/settings.py` | CORS config, `ACCOUNT_RATE_LIMITS` |
| `notifications/models.py` | `DeviceToken` model |
| `api/urls.py` | All new URL patterns |
| `requirements.txt` | `django-cors-headers==4.7.0` |

## Data Model

### DeviceToken
- `user` (FK to User)
- `token` (CharField, unique): FCM device token
- `platform` (CharField): `ios` or `android`
- `created_at` (DateTimeField)

Cleaned up on logout and when push delivery fails (stale token).

## API Endpoints

All endpoints under `/api/v1/`. Token auth via `Authorization: Token <token>`.

### Authentication

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| POST | `/api/v1/auth/login/` | No | Login, returns token |
| POST | `/api/v1/auth/signup/` | No | Create account, returns token |
| POST | `/api/v1/auth/logout/` | Token | Invalidate token |
| POST | `/api/v1/auth/password-reset/` | No | Send password reset email |

### Profile

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/v1/mobile/me/` | Token | Current user's profile |
| GET | `/api/v1/mobile/profiles/<psn>/` | Token | Any profile by PSN username |

### PSN Linking

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| POST | `/api/v1/mobile/psn/generate-code/` | Token | Generate verification code |
| POST | `/api/v1/mobile/psn/verify/` | Token | Verify PSN account |
| GET | `/api/v1/mobile/psn/status/` | Token | Link status check |

### Sync

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/v1/mobile/sync/status/` | Token | Current sync status |
| POST | `/api/v1/mobile/sync/trigger/` | Token | Trigger profile sync |

### Badges

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/v1/mobile/badges/` | Token | All badge series |
| GET | `/api/v1/mobile/badges/<series_slug>/` | Token | Badge series detail |
| GET | `/api/v1/mobile/user/badges/` | Token | Current user's badges |
| GET | `/api/v1/mobile/profiles/<psn>/badges/` | Token | User's badges by PSN |

### Games & Trophies

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/v1/mobile/profiles/<psn>/games/` | Token | Profile's game list |
| GET | `/api/v1/mobile/games/<game_id>/trophies/` | Token | Game's trophy list |

### Device Tokens

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| POST | `/api/v1/device-tokens/` | Token | Register device for push |
| DELETE | `/api/v1/device-tokens/<token>/` | Token | Unregister device |

## Gotchas and Pitfalls

- **Token auth, not session**: Mobile endpoints use `TokenAuthentication`. Do not add `SessionAuthentication` or mobile requests will fail on CSRF.
- **CORS origins**: Must include the Expo dev server URL (typically `http://localhost:19006`) in `CORS_ALLOWED_ORIGINS` env var.
- **Separate from web API**: Mobile views are in `mobile_*.py` files, not mixed with web API views. They return different response shapes optimized for mobile consumption.
- **Rate limiting**: Auth endpoints have `ACCOUNT_RATE_LIMITS` configured in settings to prevent brute-force attacks.
- **DeviceToken cleanup**: Stale tokens should be removed when push delivery fails (Phase 3 implementation detail).
- **No Celery dependency**: Push notifications in Phase 3 are planned as a Celery task, but the current backend has no Celery dependency. Token Keeper uses its own job queue system.

## Related Docs

- [API Endpoints](../reference/api-endpoints.md): Full API reference including mobile endpoints
- [Token Keeper](../architecture/token-keeper.md): Sync pipeline triggered by mobile sync endpoint
- [Notification System](../architecture/notification-system.md): Push notifications will integrate here
- [Local Setup](local-setup.md): CORS configuration for mobile development
