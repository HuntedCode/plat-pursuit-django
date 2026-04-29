# Profile Showcases

Steam-style profile customization. Users pick showcase types to feature on their profile between the identity header and the game list tabs. Up to 2 slots for free users, 5 for premium. Each showcase type can only be used once per profile. Free users get access to Platinum Trophy Case and Favorite Games; premium unlocks Badge, Rarest Trophies, Recent Platinums, Review, and Title showcases.

## Architecture Overview

The system uses a registry pattern (similar to `DASHBOARD_MODULES`): each showcase type is defined as a descriptor with metadata, a provider function that fetches display data, and an optional picker for user-controlled item selection. Adding a new showcase type means registering a descriptor, implementing a provider, and creating a template partial — no model changes.

Storage is split by complexity. Showcases with dedicated per-item tables (`UserTrophySelection`, `ProfileBadgeShowcase`) reuse those tables as-is; the `ProfileShowcase.config` JSONField stays empty for those types. Showcases with small fixed-size selection lists (favorite games, reviews, titles) store selected IDs in the JSON config. Automatic showcases (Rarest Trophies, Recent Platinums) have empty configs and derive items from earned trophies.

Premium gating happens at the slot-add layer (not at view time). Users can configure their showcases while premium, then keep enjoying them after downgrade — but premium-only showcases become `is_active=False` and can't be re-activated or reordered until they re-subscribe. Anyone can view anyone else's showcases regardless of their own subscription status.

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` (ProfileShowcase) | Polymorphic showcase model with JSON config |
| `trophies/services/showcase_service.py` | Registry, providers, validators, CRUD service, downgrade handler |
| `api/profile_showcase_views.py` | 4 REST endpoints (add/remove/reorder/update-config) |
| `trophies/views/profile_views.py` (ProfileEditorView) | `/my-pursuit/profile-editor/` editor page |
| `trophies/views/profile_views.py` (ProfileDetailView) | Adds `rendered_showcases` to profile context |
| `templates/trophies/profile_editor.html` | Two-column editor with drag-reorder and per-type pickers |
| `templates/trophies/partials/profile_detail/profile_showcases_section.html` | Container rendered on the profile page |
| `templates/trophies/partials/profile_showcases/*.html` | Per-type display templates (7 files) |
| `static/js/profile-editor.js` | Editor JS: add/remove/reorder + 4 batched pickers + rarest options |
| `users/services/subscription_service.py` | Calls `handle_premium_downgrade()` on cancel |

## Data Model

### ProfileShowcase
| Field | Type | Notes |
|-------|------|-------|
| `profile` | FK(Profile) | CASCADE |
| `showcase_type` | CharField(30) | 8 choices (7 active + 1 retained) |
| `sort_order` | PositiveSmallIntegerField | 1-based, user-controlled via drag |
| `is_active` | BooleanField | False preserves configuration across downgrade |
| `config` | JSONField | Per-type payload (see registry) |
| `created_at` / `updated_at` | DateTimeField | auto |

`unique_together = (profile, showcase_type)` enforces one-per-type per profile.

### Registered Showcase Types (v1)

| Slug | Premium | Max Items | Config Schema | Storage |
|------|---------|-----------|---------------|---------|
| `platinum_case` | No | 20 | `{}` | `UserTrophySelection` |
| `favorite_games` | No | 6 | `{"game_ids": [...]}` | JSON |
| `badge_showcase` | Yes | 5 | `{}` | `ProfileBadgeShowcase` |
| `rarest_trophies` | Yes | 6 | `{"one_per_game": bool}` | Derived |
| `recent_platinums` | Yes | 6 | `{}` | Derived |
| `review_showcase` | Yes | 2 | `{"review_ids": [...]}` | JSON |
| `title_showcase` | Yes | 6 | `{"user_title_ids": [...]}` | JSON |

`challenge_showcase` is defined as a model choice but not registered; deferred for future work. Profiles that had one saved will gracefully skip rendering (service logs a warning).

## Key Flows

### Adding a Showcase
1. User clicks "Add" on an available showcase card in the editor
2. JS POSTs to `/api/v1/profile/showcases/` with `{showcase_type: slug}`
3. `ProfileShowcaseService.add_showcase()` validates: descriptor exists, premium requirement, slot limit, uniqueness
4. Row created with `sort_order = max + 1`, `is_active=True`, empty `config`
5. Page reloads to refresh available/active lists
6. For user-controlled types, user clicks "Configure" to pick items

### Reordering
1. User drags an active showcase row (DragReorderManager on `[data-item-id]`)
2. On drop, JS POSTs slug list to `/api/v1/profile/showcases/reorder/`
3. Service validates the list matches current active showcases exactly
4. Sort order reassigned 1..N inside `select_for_update` transaction

### Updating Config (batched picker)
1. User opens a picker modal (Favorite Games, Reviews, Titles)
2. Local `selectedIds` tracks checkbox state
3. On "Save", JS POSTs `{config: {game_ids: [...]}}` to `/api/v1/profile/showcases/<slug>/config/`
4. Service runs the type's validator (ownership check, cap enforcement)
5. Valid config is persisted

### Badge Showcase (special case)
Uses the existing `/api/v1/badges/showcase/` endpoint for per-click toggles rather than batched saves. This reuses the `ProfileBadgeShowcase` storage table with its own `display_order` column. The picker filters to highest-tier-earned per `series_slug` so users don't see redundant bronze/silver/gold entries once they've earned platinum.

### Premium Downgrade
Hook lives in `SubscriptionService.deactivate_subscription()` (inside the atomic block, immediately after `update_profile_premium(False)`). Calls `handle_premium_downgrade(profile)`:
1. Fetch all showcases for the profile
2. Mark premium-only showcases `is_active=False` (configs preserved)
3. Free showcases (platinum_case, favorite_games) stay active
4. Compact `sort_order` on the remaining active rows

There is no auto-restore on re-subscribe — users re-activate via the editor. This gives them explicit control and avoids surprise reshuffles after a billing gap.

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/profile/showcases/` | User | Add showcase (body: `{showcase_type}`) |
| DELETE | `/api/v1/profile/showcases/<slug>/` | User | Remove showcase |
| POST | `/api/v1/profile/showcases/reorder/` | User | Reorder (body: `{showcase_types: [slug...]}`) |
| POST | `/api/v1/profile/showcases/<slug>/config/` | User | Update config (body: `{config: {...}}`) |

All endpoints resolve premium status via `profile.user_is_premium`.

## Integration Points

- **Profile page** renders showcases via `profile_showcases_section.html` (inserted in `profile_detail.html` after the header partial)
- **Trophy Case page** (`/profile/<user>/trophy-case/`) is still the picker UX for `platinum_case` — linked from the editor
- **Dashboard module providers** (`provide_az_challenge`, `provide_calendar_challenge`, `provide_genre_challenge`) accept an optional `challenge=` parameter so they could later serve a Challenge showcase without duplicating logic
- **Subscription lifecycle** calls `handle_premium_downgrade()` on every cancel path

## Gotchas and Pitfalls

- **Legacy Badge/Trophy cards removed**: The old hard-coded cards in `profile_detail_header.html` (lines 315-404) were removed in Phase A. Both systems would double-render if the cards came back.
- **Adding a new type**: You must (a) add a constant to `ProfileShowcase.SHOWCASE_TYPES`, (b) register the descriptor, (c) create the display template, (d) if user-controlled, add a picker in `profile_editor.html` and a handler in `profile-editor.js`. No migration is needed because `showcase_type` is a CharField with choices.
- **`json_script` rendering context**: Pickers rely on `{{ data|json_script:"id" }}` which only emits the block if the context variable is non-None. Gate each block with `{% if ... %}` so anonymous/non-eligible states skip cleanly.
- **ProfileBadgeShowcase has its own max-5 constraint** enforced in its `save()` method — respect this if you bulk-insert badges programmatically.
- **Platinum Trophy Case cap changed from 10 to 20**: `UserTrophySelection.save()` enforces 20 now. If you see older code referencing 10, check whether it's legitimate.
- **Automatic showcase validators**: Rarest Trophies has a validator (for its `one_per_game` toggle) even though it's user-controlled in terms of display options only. `is_automatic: True` in the registry is purely cosmetic (skips the Configure button fallback) — it's not a behavioral flag.
- **Challenge showcase (deferred)**: `SHOWCASE_CHALLENGE` is still a model constant, but not registered. Any old `ProfileShowcase` row with this type will be skipped at render (logged as a warning). Re-registering it in the future will instantly light up any preserved rows.

## Related Docs

- [Review Hub](review-hub.md): Review Showcase links into the hub with DLC-aware `?group=` parameter
- [Challenge Systems](challenge-systems.md): challenges are the source data for the deferred Challenge showcase
- [Payment Webhooks](../architecture/payment-webhooks.md): `deactivate_subscription()` flow where the downgrade hook fires
- [Dashboard](dashboard.md): uses the same registry pattern
