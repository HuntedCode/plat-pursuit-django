# Dashboard System

The dashboard is a private page at `/dashboard/` (login required, currently `StaffRequiredMixin` during dev) that serves as a personal trophy hunting command center. Users see a grid of **modules**, each providing a specific data view (stats, progress, highlights, etc.). Built on a Module Registry pattern where each module is a self-contained triple: Python data provider, HTML partial template, and optional JS initializer.

## Architecture Overview

The dashboard uses a **Module Registry** pattern. All modules are declared in a single `DASHBOARD_MODULES` list in `dashboard_service.py`. Each entry is a descriptor dict that binds a slug, display metadata, a Python provider callable, a template path, and behavioral flags (load strategy, caching, sizing, premium gating).

Two load strategies exist: **server-rendered** modules have their provider called during `get_context_data()` and are included inline in the page HTML. **Lazy-loaded** modules render an animated skeleton on page load, then fetch their HTML via AJAX from `/api/v1/dashboard/module/<slug>/`. This split lets cheap modules render instantly while expensive ones load in parallel without blocking the page.

The responsive grid uses 3 tiers: 2 columns at tablet (768px+), 4 at desktop (`lg:`), 6 at wide (`2xl:`). Module sizes (small/medium/large) map to grid column spans at each tier. Premium users can drag-reorder modules, resize them, and hide unlimited modules. Free users can hide up to 3.

## Current Modules

| Slug | Name | Category | Strategy | Default Size | Premium |
|------|------|----------|----------|-------------|---------|
| `trophy_snapshot` | Trophy Snapshot | at_a_glance | Server | Medium | No |
| `recent_platinums` | Recent Platinums | at_a_glance | Lazy (5m cache) | Medium | No |
| `challenge_hub` | Challenge Hub | progress | Lazy (5m cache) | Large | No |
| `badge_progress` | Badge Progress | badges | Lazy (10m cache) | Medium | No |

### Trophy Snapshot
Profile stats at a glance: platinums, golds, silvers, bronzes, total trophies, games, completions, average progress, and trophy level. Zero additional queries (all denormalized on Profile). Adapts layout by size: small shows 3 key stats, medium/large shows full grid, large adds a completion progress bar.

### Recent Platinums
Last N platinum trophies earned (3/6/10 depending on size) with game icon, title, relative date, and PSN rarity badge. Links to game detail page. Empty state encourages the user to earn their first platinum.

### Challenge Hub
Overview of all 3 challenge types with 3 states per type:
- **Active**: Progress bar, slot counts, link to detail/edit page
- **Completed**: Success badge with completion date, link to view
- **No challenge**: CTA card with flavor text and "Start Challenge" link

At large size, challenges display in a 3-column grid. At medium, they stack vertically.

### Badge Progress
Badges closest to next tier, sorted by completion percentage. Shows badge icon (layered rendering via `partials/badge.html`), series name, progress bar, and concept counts. Links to badge detail page. Empty state links to badge list. Large size includes total badge stats footer.

**Provider note**: Filters to `is_live=True` badges with `required_stages > 0` to avoid division by zero. Excludes fully earned badges (pct >= 100). Size parameter controls item limit (2/4/6).

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/dashboard_service.py` | Module registry, size system, ordering, caching, providers |
| `trophies/views/dashboard_views.py` | `DashboardView` page view (staff-gated during dev) |
| `api/dashboard_views.py` | 3 API endpoints: module data, config update, reorder |
| `trophies/models.py` | `DashboardConfig` model |
| `static/js/dashboard.js` | `DashboardManager` class (~482 lines) |
| `static/js/utils.js` | `DragReorderManager` (shared utility, `useXY: true` for 2D grids) |
| `templates/trophies/dashboard.html` | Main page template with grid container |
| `templates/trophies/partials/dashboard/customize_panel.html` | Customize modal (toggle/reorder/resize) |
| `templates/trophies/partials/dashboard/trophy_snapshot.html` | Trophy Snapshot module |
| `templates/trophies/partials/dashboard/recent_platinums.html` | Recent Platinums module |
| `templates/trophies/partials/dashboard/challenge_hub.html` | Challenge Hub module |
| `templates/trophies/partials/dashboard/badge_progress.html` | Badge Progress module |
| `tailwind.config.js` | Safelist for dynamic grid classes |

## Data Model

### DashboardConfig
- `profile` (OneToOneField to Profile, primary_key=True)
- `module_order` (JSONField, default=list): Premium custom slug order
- `hidden_modules` (JSONField, default=list): Hidden slug list (all users)
- `module_settings` (JSONField, default=dict): Premium per-module overrides (e.g. size)
- `updated_at` (DateTimeField, auto_now)

Auto-created via `get_or_create()` on first dashboard visit. NOT a Concept relation: no `absorb()` update needed.

### Module Descriptor Schema

```python
{
    'slug': 'unique_identifier',
    'name': 'Display Name',
    'description': 'Short description.',
    'category': 'at_a_glance',           # Grouping key
    'template': 'trophies/partials/dashboard/my_module.html',
    'provider': my_provider_function,     # Direct callable, NOT a string
    'requires_premium': False,
    'load_strategy': 'server' | 'lazy',
    'default_order': 1,
    'default_settings': {},
    'cache_ttl': 600,                    # Seconds (lazy modules only)
    'default_size': 'medium',
    'allowed_sizes': ['small', 'medium', 'large'],
}
```

**Categories**: `at_a_glance`, `progress`, `badges`, `community`, `highlights`, `premium`

**Validation**: `_validate_registry()` runs at import time and asserts no duplicate slugs, valid sizes, valid load strategies, and callable providers.

## Key Flows

### Page Load

1. `DashboardView.get_context_data()` fetches/creates `DashboardConfig`
2. Modules filtered by premium status, ordered by custom order or default
3. Server-rendered modules: providers called in `get_server_module_data()` batch
4. Lazy modules: skeleton placeholders rendered inline
5. Page sends module config to JS as init params
6. `DashboardManager.init()` fires `_loadLazyModules()` (parallel `Promise.allSettled`)
7. Each lazy module fetches `GET /api/v1/dashboard/module/<slug>/?size=<size>`
8. Skeleton replaced with rendered HTML via `outerHTML`
9. Module JS init callbacks fire if registered

### Module Customization (Premium)

1. User opens customize panel (dialog modal)
2. Toggle visibility: checkbox fires `_handleToggle()`, updates `hidden_modules`, debounced POST to config API
3. Resize: S/M/L buttons fire `resizeModule()`, swaps grid classes immediately, debounced POST to config API
4. Drag reorder: `DragReorderManager` handles both main grid (2D-aware) and customize list (1D). Order saved via debounced POST to reorder API
5. Reset: `_resetToDefault()` unhides all, resets sizes, atomic POST clears all fields

### Lazy Module Caching

1. Cache key: `dashboard:mod:{slug}:{profile_id}:{size}` (size-aware)
2. Each module specifies `cache_ttl` (0 = no caching)
3. Different sizes are cached independently (different item counts)
4. `invalidate_dashboard_cache(profile_id)` flushes all keys for all sizes
5. Flush via: `python manage.py redis_admin --flush-dashboard <profile_id>`

### Size-Aware Providers

Providers can optionally accept a `size` parameter to adjust their output (e.g., item count limits). The framework uses `inspect.signature()` to detect if a provider supports the `size` parameter and passes it accordingly. Providers without a `size` parameter continue to work unchanged.

```python
# Size-aware provider (receives effective size)
def provide_recent_platinums(profile, size='medium'):
    limit = SIZE_LIMITS.get(size, 6)
    ...

# Simple provider (no size parameter needed)
def provide_trophy_snapshot(profile):
    ...
```

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/dashboard/module/<slug>/?size=<size>` | Staff | Rendered HTML for lazy module |
| POST | `/api/v1/dashboard/config/` | Staff | Update hidden/settings/order |
| POST | `/api/v1/dashboard/reorder/` | Staff (Premium) | Save drag-drop order |
| POST | `/api/v1/dashboard/preview-toggle/` | Staff | Toggle premium/free preview mode |

**Config update** accepts partial payloads. `hidden_modules` works for all users (free capped at 3). `module_settings` and `module_order` are premium-only. Settings are **merged** with existing, not replaced.

## Staff Premium Preview Toggle

Staff testers can switch between "view as premium" and "view as free" using the toggle button in the dashboard header. This uses a session variable (`dashboard_preview_premium`) that overrides the real `profile.user_is_premium` across all dashboard views and API endpoints.

- **Toggle button**: In the dashboard header, shows "Premium" (gold/warning) or "Free" (ghost) with the current effective state
- **"Preview" badge**: Appears when the override is active (i.e., viewing as the opposite of your real status)
- **Session-based**: Persists across page reloads, clears on logout
- **Full coverage**: The `get_effective_premium(request)` helper is used by the main view and all 3 API endpoints, so lazy-loaded modules, config saves, and reorder all respect the override
- **No DB changes**: The override is purely in the session; the actual `user_is_premium` field is untouched

## Responsive Grid System

| Breakpoint | Columns | Small | Medium | Large |
|-----------|---------|-------|--------|-------|
| Base (768px+) | 2 | full-width | full-width | full-width |
| `lg:` (1024-1535px) | 4 | half (2/4) | half (2/4) | full (4/4) |
| `2xl:` (1536px+) | 6 | 1/3 (2/6) | 1/2 (3/6) | full (6/6) |

Grid classes are applied dynamically by JS, so they must be safelisted in `tailwind.config.js`.

At tablet (below `lg:`), ALL modules are full-width regardless of size setting. The S/M/L resize buttons are hidden below `lg:` via `hidden lg:inline-flex`.

## Integration Points

- [Token Keeper](../architecture/token-keeper.md): Sync completion could invalidate dashboard cache
- [Badge System](../architecture/badge-system.md): Badge Progress module queries `UserBadgeProgress`
- [Challenge System](../features/challenges.md): Challenge Hub module queries `Challenge` and slot models
- `DragReorderManager` in `utils.js`: Shared drag utility with `useXY: true` for 2D grid awareness

## Gotchas and Pitfalls

- **SIZE_GRID_CLASSES values are arrays in JS**: Always use spread (`...SIZE_GRID_CLASSES[size]`) at `classList.add()` call sites.
- **Customize panel must stay flat**: Category `<h4>` headers and `.customize-module-row` must be direct children of `#customize-module-list`. SortableJS needs draggable items as direct children.
- **Module settings are merged, not replaced**: The config update API merges new settings with existing ones. An empty dict `{}` resets all settings.
- **Two DragReorderManager instances**: Main grid and customize modal both use `DragReorderManager` (powered by SortableJS). Both share the same debounced order save.
- **SortableJS loaded globally**: `static/js/vendor/Sortable.min.js` is loaded in `base.html` before `utils.js`. `DragReorderManager` gracefully degrades if Sortable is not available.
- **Provider callable, not string**: Module descriptors pass actual function references, not dotted paths. This means providers must be importable at registry load time.
- **Staff-gated during dev**: Switch `StaffRequiredMixin` to `LoginRequiredMixin` and `StaffRequiredAPIMixin` to `LoginRequiredAPIMixin` for production launch.
- **Tailwind safelist**: Any new grid classes applied via JS must be added to the safelist in `tailwind.config.js`.
- **Badge progress division by zero**: Provider filters to `required_stages > 0` to avoid division by zero in percentage calculation. Megamix badges use `min_required` via `required_stages` (already denormalized).
- **Cache keys are size-aware**: Cache key format is `dashboard:mod:{slug}:{profile_id}:{size}`. Invalidation flushes all size variants.
- **Challenge Hub CTA links**: Uses Django `{% url %}` tags for challenge create/detail URLs. These must match the names in `plat_pursuit/urls.py`.
- **Premium checks must use `get_effective_premium(request)`**: Never read `profile.user_is_premium` directly in dashboard views or API endpoints. Always use the helper so the staff preview toggle works correctly.
- **Preview toggle is dev-only**: Remove or gate the toggle UI when switching from `StaffRequiredMixin` to `LoginRequiredMixin` for production launch.

## How to Add a New Module

1. **Write the provider**: `def my_provider(profile, size='medium') -> dict` in `dashboard_service.py`. Accept `size` if the output varies by size.
2. **Register in DASHBOARD_MODULES**: Add descriptor dict with slug, template, provider, sizes, etc.
3. **Create the template**: Use `{{ data.* }}` for provider data, `{{ effective_size }}` for layout adaptation. Follow existing module templates for consistent card styling.
4. **Optional JS init**: `dashboard.registerModuleInit('slug', (containerEl) => { ... })` for post-load behavior
5. **Choose server vs lazy**: Server for zero-query data (Profile fields), lazy for anything with DB hits (use `cache_ttl`)

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `dashboard:mod:{slug}:{profile_id}:{size}` | Per-module `cache_ttl` | Lazy module data cache (per size) |

## Related Docs

- [Data Model](../architecture/data-model.md): DashboardConfig model details
- [JS Utilities](../reference/js-utilities.md): DragReorderManager shared utility
- [Template Architecture](../reference/template-architecture.md): Partial template patterns
- [Challenges](../features/challenges.md): Challenge model and slot systems
- [Badge System](../architecture/badge-system.md): Badge, UserBadgeProgress, layered rendering
