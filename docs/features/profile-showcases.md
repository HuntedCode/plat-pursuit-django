# Profile Showcases

> **DELETED 2026-08, not hidden.** An earlier version of this banner promised the models, service, API
> views, editor page and JS were only parked. They are gone: `ProfileShowcase` and `ProfileBadgeShowcase`
> were dropped by migration `0303`, along with `showcase_service.py`, `api/profile_showcase_views.py` and
> `profile_editor.html`. The cited `tests/engine/test_showcases_hidden.py` no longer exists either; the
> live invariants moved into `tests/engine/test_profile_banner_retired.py`.
>
> Kept as the record of what the feature was and why it went. Rebuilding means designing it again, not
> un-commenting anything.

> **HIDDEN (2026-08) pending a ground-up rebuild of profile customization.** The profile no longer
> renders the showcase band, `/profile-editor/` 302s to the homepage, and the four write endpoints are
> withdrawn. **No data was touched** -- every `ProfileShowcase`, `UserTrophySelection` and
> `ProfileBadgeShowcase` row is intact, as are the models, the service, the API views, all five display
> partials, the editor page and its JS. Restoring the surface is reopening those doors: put back the
> `profile_showcases_section.html` include in `profile_detail.html`, the `rendered_showcases` context in
> `ProfileDetailView`, the `/profile-editor/` route, and the four routes in `api/urls.py`.
>
> Why: the About surface these were going to anchor turned out to pair them with a trophy timeline that
> had rendered nowhere since the header rebuild, and there is a second, competing customization story in
> the Pursuer Card's own showcase slot. Rather than hide working customization behind a tab click to
> pair it with a husk, the whole surface comes off and gets rebuilt deliberately.
> Pinned by `tests/engine/test_showcases_hidden.py`.

Steam-style profile customization. Users pick showcase types to feature on their profile between the identity header and the game list tabs. Up to 2 slots for free users, 5 for premium. Each showcase type can only be used once per profile. Free users get access to Platinum Trophy Case and Favorite Games; premium unlocks Badge, Recent Platinums, and Title showcases.

## Architecture Overview

The system uses a registry pattern (similar to `DASHBOARD_MODULES`): each showcase type is defined as a descriptor with metadata and a provider function that fetches display data. (Descriptors used to carry an `editor_template` key pointing into a `trophies/partials/profile_editor/` directory that has never existed; nothing read it and it was removed. The real pickers are branches in `profile_editor.html`.) Adding a new showcase type means registering a descriptor, implementing a provider, and creating a template partial — no model changes.

Storage is split by complexity. Showcases with dedicated per-item tables (`UserTrophySelection`, `ProfileBadgeShowcase`) reuse those tables as-is; the `ProfileShowcase.config` JSONField stays empty for those types. Showcases with small fixed-size selection lists (favorite games, titles) store selected IDs in the JSON config. Automatic showcases (Recent Platinums) have empty configs and derive items from earned trophies.

Premium gating happens at the slot-add layer (not at view time). Users can configure their showcases while premium, then keep enjoying them after downgrade — but premium-only showcases become `is_active=False` and can't be re-activated or reordered until they re-subscribe. Anyone can view anyone else's showcases regardless of their own subscription status.

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` (ProfileShowcase) | Polymorphic showcase model with JSON config |
| `trophies/services/showcase_service.py` | Registry, providers, validators, CRUD service, downgrade handler |
| `api/profile_showcase_views.py` | 4 REST endpoints (add/remove/reorder/update-config) |
| `trophies/views/profile_views.py` (ProfileEditorView) | The editor page. Parked: `/profile-editor/` now redirects home |
| `trophies/views/profile_views.py` (ProfileDetailView) | Adds `rendered_showcases` to profile context |
| `templates/trophies/profile_editor.html` | Two-column editor with drag-reorder and per-type pickers |
| `templates/trophies/partials/profile_detail/profile_showcases_section.html` | Container rendered on the profile page |
| `templates/trophies/partials/profile_showcases/*.html` | Per-type display templates (5 live + an orphan `showcase_reviews.html`) |
| `static/js/profile-editor.js` | Editor JS: add/remove/reorder + 3 batched pickers |
| `users/services/subscription_service.py` | Calls `handle_premium_downgrade()` on cancel |

## Data Model

### ProfileShowcase
| Field | Type | Notes |
|-------|------|-------|
| `profile` | FK(Profile) | CASCADE |
| `showcase_type` | CharField(30) | 5 choices, all registered and renderable |
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
| `recent_platinums` | Yes | 6 | `{}` | Derived |
| `title_showcase` | Yes | 6 | `{"user_title_ids": [...]}` | JSON |

**Three types have been removed and must not be re-added casually.** `review_showcase` went with the text-review archive (migration `0237`). `rarest_trophies` was deleted outright after the 2026-08-09 outage -- it was the only DERIVED type, ranking the profile's entire earned set on a joined column (migration `0275`; see the warning below and `tests/engine/test_anon_profile_render.py`). `challenge_showcase` went with the Challenges retirement (migration `0292`), and had never been renderable anyway: it was offered as a model choice but never registered, so a stored row displayed nothing. `tests/engine/test_profile_banner_retired.py` now pins that the model's choices and the registry agree in both directions, which is what would have caught it.

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
1. User opens a picker modal (Favorite Games, Titles)
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

> **All four of these routes are WITHDRAWN** (2026-08) along with the rest of the surface -- they 404
> today. The views are parked in `api/profile_showcase_views.py`; restoring them is putting the four
> `path()` lines and the import back in `api/urls.py`. Documented as they were, because that is what a
> restore has to recreate.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/profile/showcases/` | User | Add showcase (body: `{showcase_type}`) |
| DELETE | `/api/v1/profile/showcases/<slug>/` | User | Remove showcase |
| POST | `/api/v1/profile/showcases/reorder/` | User | Reorder (body: `{showcase_types: [slug...]}`) |
| POST | `/api/v1/profile/showcases/<slug>/config/` | User | Update config (body: `{config: {...}}`) |

All endpoints resolve premium status via `profile.user_is_premium`.

## Integration Points

- **Profile page** rendered showcases via `profile_showcases_section.html`, included in `profile_detail.html` after the header partial. That include is currently commented out (see the banner at the top); the partial itself is unchanged.
- **Trophy Case page** (`/hunters/<psn_username>/trophy-case/`) is still the picker UX for `platinum_case` — linked from the editor
- **Subscription lifecycle** calls `handle_premium_downgrade()` on every cancel path

## Gotchas and Pitfalls

- **Legacy Badge/Trophy cards removed**: The old hard-coded cards in `profile_detail_header.html` (lines 315-404) were removed in Phase A. Both systems would double-render if the cards came back.
- **Adding a new type**: You must (a) add a constant to `ProfileShowcase.SHOWCASE_TYPES`, (b) register the descriptor, (c) create the display template, (d) if user-controlled, add a picker in `profile_editor.html` and a handler in `profile-editor.js`. No migration is needed because `showcase_type` is a CharField with choices.
- **`json_script` rendering context**: Pickers rely on `{{ data|json_script:"id" }}` which only emits the block if the context variable is non-None. Gate each block with `{% if ... %}` so anonymous/non-eligible states skip cleanly.
- **ProfileBadgeShowcase has its own max-5 constraint** enforced in its `save()` method — respect this if you bulk-insert badges programmatically.
- **Platinum Trophy Case cap changed from 10 to 20**: `UserTrophySelection.save()` enforces 20 now. If you see older code referencing 10, check whether it's legitimate.
- **`is_automatic` is cosmetic.** It only skips the Configure-button fallback in the editor; it is not a behavioral flag and nothing in the service branches on it.
- **A derived showcase must stay bounded.** `rarest_trophies` was retired in 2026-08 (migration `0275`) because it was the one type whose item set came from ranking the profile's ENTIRE earned set, on a joined column (`trophy__trophy_earn_rate`) that no index could serve. One showcase cost more than the other five combined on a large account, and it ran on every profile render including anonymous ones. `recent_platinums` is also derived but stays cheap because it reads a date-ordered index and stops at 6. Before adding a derived type, ask what it costs on a 250K-trophy profile: if the answer scales with the account rather than with the slot count, it does not belong here.
- **An offered type must be a registered type.** `challenge_showcase` was a model choice that was never registered, so a row of it could be stored and would then render nothing -- silently, with only a service warning. It is gone (migration `0292`), and `tests/engine/test_profile_banner_retired.py` now asserts the model's choices and `SHOWCASE_REGISTRY` match in both directions.

## Related Docs

- [Payment Webhooks](../architecture/payment-webhooks.md): `deactivate_subscription()` flow where the downgrade hook fires
- [Dashboard](dashboard.md): uses the same registry pattern
