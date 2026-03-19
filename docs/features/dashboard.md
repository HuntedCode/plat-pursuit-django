# Dashboard System

The dashboard is the personal trophy hunting command center at `/dashboard/`. It will serve as the index page for all logged-in users (currently `StaffRequiredMixin` during dev). Modules are organized into a **tabbed navigation system** with 6 immutable system tabs and support for premium user-created custom tabs. Only the active tab's lazy modules load on page init for performance.

## Architecture Overview

The dashboard uses a **Module Registry** pattern with a **Tabbed Carousel** layout. Modules are declared in `DASHBOARD_MODULES` in `dashboard_service.py`. Each module belongs to a category that maps to a default system tab. Premium users can create custom tabs and move modules between tabs.

**Layout**: Single-column `flex flex-col max-w-4xl mx-auto` within each tab panel. Drag reorder via SortableJS controls vertical priority within tabs. Premium users can reorder modules, configure per-module settings, create/rename/delete custom tabs, and move modules between tabs.

**Performance**: Only the active tab's lazy modules load on page init. Other tabs load on first activation. Cache keys include a settings hash so different configurations are cached independently. Mutation points (challenge create/delete, badge sync) invalidate the dashboard cache.

## Tab System

### System Tabs (6, immutable for all users)

| Order | Icon | Tab | Slug | Default Modules |
|-------|------|-----|------|-----------------|
| 1 | Crown | Premium | `premium` | Premium-exclusive modules |
| 2 | Trophy Cup | At a Glance | `at_a_glance` | Trophy Snapshot, Recent Platinums |
| 3 | Chart | Progress | `progress` | Challenge Hub |
| 4 | Medal | Badges | `badges` | Badge Progress |
| 5 | Star | Highlights | `highlights` | My Reviews, Rate My Games |
| 6 | Share | Share & Export | `share` | Badge Showcase, Profile Card, Latest Platinum, Challenge Cards, Recap Card |

### Custom Tabs (Premium only, max 6)

Premium users can create custom tabs with a name (max 20 chars) and icon (from 8 presets). Custom tabs appear in a second, smaller tab bar below the system tabs. Tabs can be renamed, deleted, and reordered via drag in the customize panel.

### Tab Navigation

- **System tab bar**: `flex-1` buttons that fill the width evenly, icon + short label on desktop, icon-only on tablet
- **Custom tab bar**: Smaller, accent-colored, only renders if custom tabs exist
- **Keyboard**: ArrowLeft/Right/Home/End navigate between tabs
- **Active tab persistence**: Last active tab saved to `DashboardConfig.tab_config.active_tab`, defaults to `at_a_glance` on first visit or after reset
- **Per-tab lazy loading**: `loadedTabs` Set tracks which tabs have been loaded

## Current Modules

| Slug | Name | Category | Strategy | Cache | Premium |
|------|------|----------|----------|-------|---------|
| `trophy_snapshot` | Trophy Snapshot | at_a_glance | Server | None | No |
| `recent_platinums` | Recent Platinums | at_a_glance | Lazy | 5m | No |
| `challenge_hub` | Challenge Hub | progress | Lazy | 5m | No |
| `badge_progress` | Badge Progress | badges | Lazy | 10m | No |
| `recent_badges` | Recent Badges | badges | Lazy | 10m | No |
| `recent_activity` | Recent Activity | at_a_glance | Lazy | 5m | No |
| `monthly_recap_preview` | Monthly Recap Preview | highlights | Lazy | 30m | No |
| `quick_settings` | Quick Settings | at_a_glance | Server | None | No |
| `badge_stats` | Badge Stats | badges | Lazy | 10m | No |
| `badge_xp_leaderboard` | Badge XP & Leaderboard | badges | Lazy | 10m | No |
| `country_xp_leaderboard` | Country XP Leaderboard | badges | Lazy | 10m | No |
| `az_challenge` | A-Z Challenge | progress | Lazy | 5m | No |
| `genre_challenge` | Genre Challenge | progress | Lazy | 5m | No |
| `calendar_challenge` | Platinum Calendar | progress | Lazy | 5m | No |
| `completion_milestones` | Almost There | progress | Lazy | 10m | No |
| `milestone_tracker` | Milestone Tracker | progress | Lazy | 10m | No |
| `my_reviews` | My Reviews | highlights | Lazy | 10m | No |
| `rarity_showcase` | Rarity Showcase | highlights | Lazy | 10m | No |
| `rate_my_games` | Rate My Games | highlights | Lazy | 30m | No |
| `badge_showcase` | Badge Showcase | share | Lazy | 10m | No |
| `profile_card_preview` | Profile Card | share | Lazy | None | No |
| `recent_platinum_card` | Latest Platinum | share | Lazy | 10m | No |
| `challenge_share_cards` | Challenge Cards | share | Lazy | 10m | No |
| `recap_share_card` | Recap Card | share | Lazy | 30m | No |

See [Module Catalog](../design/dashboard-module-catalog.md) for the full module roadmap.

## Per-Module Settings Framework

Premium users can configure individual modules via the customize panel. Each module declares `configurable_settings` with two types:
- **`select`**: Button group (e.g., item count: 3/6/10)
- **`toggle`**: On/off switch

Settings are stored in `DashboardConfig.module_settings` as `{slug: {key: value}}`. `get_effective_settings()` resolves user overrides against defaults with validation. Free users see a locked gear icon as a premium teaser. Settings persist across premium downgrades but only take effect while premium.

## Customize Panel

The customize panel is a modal with three sections:

### 1. Custom Tabs Management (Premium)
- Create tab: name input (20 char max) + icon picker (8 presets) + create button
- Existing tabs: drag to reorder, rename button, delete button
- Free users see a locked teaser with upgrade prompt

### 2. Module List (Grouped by Tab)
Each tab is a collapsible section listing its modules. Each module row shows:
- Drag handle (premium)
- Module name + premium badge
- Settings gear (premium, or locked teaser for free)
- "Move to" dropdown (premium, all tabs including Premium)
- Toggle switch (all users, free capped at 3 hidden)

### 3. Footer
- Reset to Default: clears all customizations (hidden, settings, order, custom tabs, module moves)
- Done: closes modal, triggers page reload if structural changes were made

**Structural change tracking**: A `_customizeDirty` flag tracks tab creates/renames/deletes, module moves, and tab reorder. The page reloads automatically when the modal closes if any structural changes were made.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/dashboard_service.py` | Module registry, providers, tab functions, settings framework, caching |
| `trophies/views/dashboard_views.py` | `DashboardView` with tab context |
| `api/dashboard_views.py` | 4 API endpoints: module data, config, reorder, preview toggle |
| `api/user_settings_views.py` | `UpdateQuickSettingsAPIView` for dashboard Quick Settings auto-save |
| `trophies/models.py` | `DashboardConfig` model with `tab_config` field |
| `static/js/dashboard.js` | `DashboardManager`: tabs, lazy loading, customize, tab management, settings, drag |
| `static/js/vendor/Sortable.min.js` | SortableJS library |
| `static/js/utils.js` | `DragReorderManager` (SortableJS wrapper) |
| `templates/trophies/dashboard.html` | Main page: header, tab bars, tab panels, JS init |
| `templates/trophies/partials/dashboard/customize_panel.html` | Customize modal |
| `templates/trophies/partials/dashboard/tab_icon.html` | Tab icon SVG partial (8 icons) |
| `templates/trophies/partials/dashboard/*.html` | Module partial templates |

## Data Model

### DashboardConfig
- `profile` (OneToOneField to Profile, primary_key=True)
- `module_order` (JSONField, default=list): Module slug order (premium)
- `hidden_modules` (JSONField, default=list): Hidden slugs (free: max 3)
- `module_settings` (JSONField, default=dict): Per-module settings (premium)
- `tab_config` (JSONField, default=dict): Tab layout configuration
- `updated_at` (DateTimeField, auto_now)

### tab_config Structure
```json
{
    "active_tab": "at_a_glance",
    "tab_order": ["premium", "at_a_glance", "custom_1", ...],
    "custom_tabs": {
        "custom_1234_abcd": {"name": "My Favorites", "icon": "heart"}
    },
    "module_tab_overrides": {
        "badge_progress": "custom_1234_abcd"
    }
}
```

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/dashboard/module/<slug>/` | Staff | Rendered HTML for lazy module |
| POST | `/api/v1/dashboard/config/` | Staff | Update hidden/settings/order/tab_config |
| POST | `/api/v1/dashboard/reorder/` | Staff (Premium) | Save drag-drop order |
| POST | `/api/v1/dashboard/preview-toggle/` | Staff | Toggle premium/free preview |
| POST | `/api/v1/user/quick-settings/` | Staff | Quick Settings auto-save (toggles, timezone, region) |

### Config Update Behavior
- `hidden_modules`: All users (free capped at 3)
- `module_settings`: Premium only, merged with existing
- `module_order`: Premium only
- `tab_config.active_tab`: All users
- `tab_config.tab_order/custom_tabs/module_tab_overrides`: Premium only
- Custom tab names validated: non-empty, max 20 chars
- Custom tab icons validated against `VALID_TAB_ICONS`
- Custom tab slugs cannot collide with `DEFAULT_TAB_ORDER`
- Max 6 custom tabs enforced server-side

## Caching

Cache keys: `dashboard:mod:{slug}:{profile_id}:{settings_hash}` where `settings_hash` is an MD5 of the module's effective settings. Invalidation uses `cache.delete_pattern()` with a wildcard prefix to clear all variants for a given module and profile.

**Invalidation points:**
- `Challenge.soft_delete()` in `trophies/models.py`
- `create_az_challenge()`, `create_calendar_challenge()`, `create_genre_challenge()` in `challenge_service.py`
- `check_profile_badges()` in `badge_service.py` (after sync)
- `check_all_milestones_for_user()` in `milestone_service.py` (covers milestone tracker + reviews via milestone checks)
- `award_milestone_directly()` in `milestone_service.py`

## Staff Premium Preview Toggle

Staff can switch between "view as premium" and "view as free" via a header button. Uses session variable `dashboard_preview_premium`. The `get_effective_premium(request)` helper is used by all views and API endpoints.

## Gotchas and Pitfalls

- **Premium checks use `get_effective_premium(request)`**: Never read `profile.user_is_premium` directly.
- **No model instances in provider return data**: Cache serialization fails with Django model objects.
- **Settings only active for premium**: Free users get defaults. Saved settings preserved across downgrades.
- **Calendar weekday offset**: Uses `""|ljust:offset` (not `"x"|ljust:offset`) to avoid off-by-one.
- **Badge prerequisite filtering**: Provider fetches 3x limit, pre-fetches earned badge IDs, filters in Python.
- **Premium modules are visually tagged**: Premium modules show a "Premium" badge on their name. Premium users can move them to any tab freely.
- **Custom tab slug generation**: Uses `Date.now() + random(4)` to prevent collision.
- **Customize panel structural changes**: `_customizeDirty` flag triggers page reload on modal close.
- **Tab sections in customize panel**: Settings panel div must have `data-settings-slug` attribute and closing `>`.
- **Toast visibility in modals**: `ToastManager` detects open `<dialog>` elements and redirects toasts to `.modal-toast-container` inside them. Without this, browser top layer renders the dialog above all z-indices, hiding toasts.
- **Drag reorder in customize panel**: Each `.tab-section-content` gets its own SortableJS instance (not the outer list). Module rows must be direct children of their section container.
- **Reset requires confirmation**: `_resetToDefault()` shows a `confirm()` dialog before clearing all customizations.
- **Header shows equipped title**: `profile.displayed_title` (queries `UserTitle` with `is_displayed=True`). Returns `None` if no title equipped.
- **Dropdown uses short tab names**: `all_tab_options` uses `short_name` (e.g., "Progress") not full `name` (e.g., "Progress & Challenges").
- **Recent Activity groups by game+day**: Trophies for the same game on the same day are grouped into a single event showing type counts (gold/silver/bronze). Platinums are always standalone events. Badges are never grouped. Grouping is timezone-aware using the user's configured timezone.
- **Monthly Recap shows finalized recaps only**: The provider fetches the most recent finalized recap, never the current in-progress month. This avoids spoiling the full recap experience.
- **Quick Settings auto-save**: Each toggle/select change POSTs to `/api/v1/user/quick-settings/`. Timezone changes also un-finalize the current month's recap (handled server-side). Whitelisted settings prevent arbitrary field injection.
- **Quick Settings region selector**: Includes "Any" (empty string) plus 6 region codes (NA, EU, JP, AS, KR, CN). Stored as `default_region` on Profile.
- **Challenge module `_find_challenge` helper**: Shared private function used by `provide_challenge_hub` and the 3 standalone challenge providers. Finds active challenge first, falls back to most recently completed.
- **Badge Stats collection rate**: Counts unique `series_slug` with `tier=1` and `is_live=True` for the denominator. Uses Tier 1 count to avoid inflating with multi-tier series.
- **Badge XP leaderboard neighborhood**: When user is outside top 5, shows top 3 + gap + 2 above/user/2 below. Edge case: if user rank overlaps with top 3 window, `neighborhood_start = max(3, idx - 2)` prevents duplicate entries.
- **Challenge "most recent plat"**: Uses `slot.completed_at` timestamp (not alphabetical order) to find the true most recently completed slot.
- **Calendar 3-month pagination**: All 12 months are rendered in HTML, JS shows/hides 3 at a time. `_initCalendarPagination` registered via `registerModuleInit('calendar_challenge', ...)`. Defaults to the page containing the current month.
- **Milestone Tracker Python-side sort**: Completion pct requires dividing `progress_value / milestone.required_value` which crosses an FK boundary. Computed in Python since users have <50 progress records.
- **Milestone image.url extraction**: `milestone.image` is an ImageField. Must extract `.url` string in the provider, not pass the FieldFile object (not serializable for cache).
- **Almost There hidden game filtering**: Always excludes `user_hidden=True`. Additionally excludes `hidden_flag=True` only if `profile.hide_hiddens` is enabled.
- **Almost There configurable threshold**: Default 90%, options 80/90/95. Stored in `module_settings` and included in cache key hash.
- **My Reviews aggregate fallback**: Django `Sum()` returns `None` for empty querysets. Provider handles with `or 0` on all aggregate values.
- **Cache invalidation coverage**: Milestone tracker invalidated via `check_all_milestones_for_user` hook. Reviews invalidated via same (milestone check called in create/delete). Almost There and Rarity Showcase covered by existing sync pipeline.
- **Rate My Games ticker strip**: Auto-scrolling CSS animation with duplicated icons for seamless loop. Pauses on hover. Icons are clickable links to review_hub. Defensive slug check prevents crash on concepts without slugs.
- **Rarity Showcase 2-column grid**: Shows trophy icon with overlapping game icon badge. Includes trophy description (`trophy_detail`). Even limit options (4/6/8). Filters `earn_rate > 0`. Uses `rarity_color_hex` filter.
- **Share card preview pattern**: All share card modules (platinum, challenge, recap) use `_initShareCards()` in dashboard.js. Finds `.share-card-preview` containers by `data-share-html-url`, fetches HTML via API, scales to 1200x630 aspect ratio. Theme switching is client-side via `applyTheme()` (modifies DOM directly). Game art themes are excluded unless the preview has `data-supports-game-art="true"` (platinum card only). Clicking a preview opens a full-size modal (`<dialog>`). Download buttons use `data-share-png-url` with the selected theme key.
- **Share card rating prompt**: The platinum card download button triggers a rate-before-download modal (same `#rate-before-download-modal` partial as shareables page) if the user hasn't rated the game. Rating metadata (`concept_id`, `has_rating`, `is_shovelware`, `playtime`) is captured from the HTML API response and stored on the preview element's dataset. Prompted once per session per concept.
- **Share card identity bars**: All share card templates (platinum, A-Z, calendar, genre, recap) include a rich identity bar with avatar (glow border, Plus subscriber badge), username, card type label, and "Platinum Pursuit" branding. Avatar and `is_plus` are passed from the view layer. `data-element` attributes are preserved for Playwright theme rendering.
- **Share tab is last**: `DEFAULT_TAB_ORDER` places share at the end (after highlights).
- **Staff-gated during dev**: Switch mixins to `LoginRequiredMixin` for production. Remove preview toggle UI.

## How to Add a New Module

1. **Write the provider**: `def my_provider(profile, settings=None) -> dict` in `dashboard_service.py`
2. **Register in DASHBOARD_MODULES**: slug, template, provider, `configurable_settings`, `default_settings`, cache TTL, category
3. **Create the template**: `{{ data.* }}` for provider data. Card styling: `card bg-base-200/80 border-2 ...`, padding `p-5 lg:p-7`
4. **Add cache invalidation**: `invalidate_dashboard_cache()` at data mutation points
5. **Update the catalog**: `docs/design/dashboard-module-catalog.md`
6. The module's `category` determines which default tab it appears in

## Related Docs

- [Module Catalog](../design/dashboard-module-catalog.md): Full 28-module roadmap
- [Data Model](../architecture/data-model.md): DashboardConfig model
- [JS Utilities](../reference/js-utilities.md): DragReorderManager
- [Challenge Systems](../features/challenge-systems.md): Challenge models and services
- [Badge System](../architecture/badge-system.md): Badge, UserBadgeProgress, layered rendering
