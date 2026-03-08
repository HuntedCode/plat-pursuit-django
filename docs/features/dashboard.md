# Dashboard System

The dashboard is a private page at `/dashboard/` (login required, currently `StaffRequiredMixin` during dev) that serves as a personal trophy hunting command center. Users see a grid of **modules**, each providing a specific data view (stats, progress, highlights, etc.). Built on a Module Registry pattern where each module is a self-contained triple: Python data provider, HTML partial template, and optional JS initializer.

## Architecture Overview

The dashboard uses a **Module Registry** pattern. All modules are declared in a single `DASHBOARD_MODULES` list in `dashboard_service.py`. Each entry is a descriptor dict that binds a slug, display metadata, a Python provider callable, a template path, and behavioral flags (load strategy, caching, sizing, premium gating).

Two load strategies exist: **server-rendered** modules have their provider called during `get_context_data()` and are included inline in the page HTML. **Lazy-loaded** modules render an animated skeleton on page load, then fetch their HTML via AJAX from `/api/v1/dashboard/module/<slug>/`. This split lets cheap modules render instantly while expensive ones load in parallel without blocking the page.

The responsive grid uses 3 tiers: 2 columns at tablet (768px+), 4 at desktop (`lg:`), 6 at wide (`2xl:`). Module sizes (small/medium/large) map to grid column spans at each tier. Premium users can drag-reorder modules, resize them, and hide unlimited modules. Free users can hide up to 3.

**Current state**: Framework complete with 3 placeholder test modules. No real data modules built yet.

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
| `templates/trophies/partials/dashboard/placeholder_server.html` | Test: server-rendered module |
| `templates/trophies/partials/dashboard/placeholder_lazy.html` | Test: lazy-loaded module |
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

**Categories**: `at_a_glance`, `progress`, `highlights`, `community`, `historical`, `quick_links`

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

1. Cache key: `dashboard:mod:{slug}:{profile_id}`
2. Each module specifies `cache_ttl` (0 = no caching)
3. `invalidate_dashboard_cache(profile_id)` flushes all keys for a profile
4. Flush via: `python manage.py redis_admin --flush-dashboard <profile_id>`

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/dashboard/module/<slug>/?size=<size>` | Staff | Rendered HTML for lazy module |
| POST | `/api/v1/dashboard/config/` | Staff | Update hidden/settings/order |
| POST | `/api/v1/dashboard/reorder/` | Staff (Premium) | Save drag-drop order |

**Config update** accepts partial payloads. `hidden_modules` works for all users (free capped at 3). `module_settings` and `module_order` are premium-only. Settings are **merged** with existing, not replaced.

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
- [Badge System](../architecture/badge-system.md): Badge progress modules will query `UserBadgeProgress`
- [Gamification](../architecture/gamification.md): XP display module will use `ProfileGamification`
- `DragReorderManager` in `utils.js`: Shared drag utility with `useXY: true` for 2D grid awareness

## Gotchas and Pitfalls

- **SIZE_GRID_CLASSES values are arrays in JS**: Always use spread (`...SIZE_GRID_CLASSES[size]`) at `classList.add()` call sites.
- **Customize panel must stay flat**: Category `<h4>` headers and `.customize-module-row` must be direct children of `#customize-module-list`. Wrapping them in divs breaks `DragReorderManager` placeholder insertion.
- **Module settings are merged, not replaced**: The config update API merges new settings with existing ones. An empty dict `{}` resets all settings.
- **Two DragReorderManager instances**: Main grid uses `useXY: true` (2D). Customize modal uses default Y-only. Both share the same debounced order save.
- **Provider callable, not string**: Module descriptors pass actual function references, not dotted paths. This means providers must be importable at registry load time.
- **Staff-gated during dev**: Switch `StaffRequiredMixin` to `LoginRequiredMixin` and `StaffRequiredAPIMixin` to `LoginRequiredAPIMixin` for production launch.
- **Placeholder modules**: 3 test modules exist (`placeholder_server`, `placeholder_lazy`, `placeholder_premium`). Remove from `DASHBOARD_MODULES` once real modules are built.
- **Tailwind safelist**: Any new grid classes applied via JS must be added to the safelist in `tailwind.config.js`.

## How to Add a New Module

1. **Write the provider**: `def my_provider(profile) -> dict` in `dashboard_service.py`
2. **Register in DASHBOARD_MODULES**: Add descriptor dict with slug, template, provider, sizes, etc.
3. **Create the template**: Use `{{ data.* }}` for provider data, `{{ effective_size }}` for layout adaptation
4. **Optional JS init**: `dashboard.registerModuleInit('slug', (containerEl) => { ... })` for post-load behavior
5. **Choose server vs lazy**: Server for trivial queries, lazy for anything with DB hits (use `cache_ttl`)

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `dashboard:mod:{slug}:{profile_id}` | Per-module `cache_ttl` | Lazy module rendered HTML cache |

## Related Docs

- [Data Model](../architecture/data-model.md): DashboardConfig model details
- [JS Utilities](../reference/js-utilities.md): DragReorderManager shared utility
- [Template Architecture](../reference/template-architecture.md): Partial template patterns
