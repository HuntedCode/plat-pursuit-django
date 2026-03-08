# Template Architecture

The template system uses Django's template inheritance with a single `base.html` root, context processors for global data, custom templatetags for display logic, and view mixins for access control. The zoom wrapper system handles responsive scaling for sub-768px screens.

## Base Template (base.html)

### Block Structure

| Block | Purpose | Default |
|-------|---------|---------|
| `title` | Page title | "Platinum Pursuit" |
| `meta_description` | SEO meta description | Site description |
| `og_title`, `og_description`, `og_image` | Open Graph social media | Site defaults |
| `twitter_card_type`, `twitter_title`, `twitter_description`, `twitter_image` | Twitter Card | Site defaults |
| `canonical_url` | SEO canonical link | Empty |
| `extra_head` | Additional head content (fonts, styles) | Empty |
| `content` | Main page content | Empty |
| `fixed_overlays` | Modals/overlays (outside zoom wrapper) | Empty |
| `js_scripts` | Page-specific JavaScript (after utilities) | Empty |

### Page Structure

```
<body>
  <div id="zoom-container">
    <div id="zoom-wrapper" class="min-h-screen flex flex-col">
      navbar
      site banner (optional)
      fundraiser banner (optional)
      premium upsell banner (free users)
      <main> (3-column: left ad | content block | right ad) </main>
      footer
    </div>
  </div>
  back-to-top button          <!-- Fixed, outside wrapper -->
  mobile tabbar               <!-- Fixed, outside wrapper -->
  toast container              <!-- Fixed, outside wrapper -->
  {% block fixed_overlays %}   <!-- Modals, outside wrapper -->
</body>
```

### Script Loading Order

1. `utils.js` (shared utilities)
2. `celebrations.js` (confetti effects)
3. `notifications.js` (notification dropdown)
4. `main.js` (global behaviors)
5. `easter-eggs.js` (hidden features)
6. `{% block js_scripts %}` (page-specific)

### Key Features

- **Dark theme**: localStorage persistence, loads before DOM paint to prevent flash
- **AdSense**: Guttered by staff status, API paths, and premium subscription
- **Background images**: Supports concept-specific backgrounds via `image_urls.bg_url` context
- **Premium theme gradients**: CSS custom properties via `user_theme_style` context

## Zoom Wrapper System

Every page has the wrapper divs (`#zoom-container` > `#zoom-wrapper`) but scaling is **dormant** until activated. The CSS rules in `input.css` are gated behind `#zoom-container.zoom-active`.

To opt a page into scaling:
```js
PlatPursuit.ZoomScaler.init();  // in {% block js_scripts %}
```

This adds `.zoom-active` and runs height correction. Below 768px, the page renders at 768px width and `transform: scale()` shrinks it to fit. Fixed-position elements (toasts, modals, tabbar) live **outside** the wrapper so they aren't affected.

See CLAUDE.md for full responsive design standards.

## Context Processors

All defined in `plat_pursuit/context_processors.py`:

| Processor | Provides | Purpose |
|-----------|----------|---------|
| `ads` | `ADSENSE_ENABLED`, `ADSENSE_PUB_ID`, `ADSENSE_TEST_MODE` | Control ad visibility |
| `moderation` | `pending_reports_count`, `pending_proposals_count` | Staff dashboard badge counts (60s cache) |
| `premium_theme_background` | `user_theme_style` | Premium gradient theme as CSS string |
| `active_fundraiser` | `active_fundraiser` | Live fundraiser for site banner (60s cache) |
| `high_sync_volume` | `high_sync_volume`, `high_sync_volume_count`, `high_sync_volume_activated_at` | Redis flag for sync volume banner |

## Custom Templatetags

### core/templatetags/query_tags.py

| Tag | Purpose |
|-----|---------|
| `query_transform(**kwargs)` | Update GET params, preserve others |
| `query_transform_key(key, value)` | Dynamic key name param update |
| `querystring(exclude=None)` | Full encoded query string |

### core/templatetags/custom_filters.py (54+ filters)

| Category | Filters |
|----------|---------|
| Date/Time | `iso_naturaltime`, `iso_datetime`, `timedelta_hours` |
| Colors | `platform_color`, `platform_color_str`, `platform_color_hex`, `region_color_hex`, `trophy_color`, `trophy_css_color`, `badge_color`, `rarity_color_hex` |
| Trophy/Badge | `trophy_rarity_label`, `badge_tier`, `badge_tier_xp`, `psn_rarity` |
| Data access | `dict_get`, `get_item` |
| Formatting | `multiply`, `format_date`, `sync_status_display`, `moderator_display_name`, `tojson` |
| Markup | `parse_spoilers` (Discord-style `||text||`), `gradient_themes_json` |

**Safety**: `parse_spoilers` escapes input first, `tojson` escapes HTML-critical chars, `format_date` respects user's 24h clock preference.

### trophies/templatetags/trophy_tags.py

| Tag | Purpose |
|-----|---------|
| `get_trophy(trophy_id)` | Fetch single trophy |
| `get_trophy_group(trophy)` | Get DLC group (excludes 'default') |
| `get_trophy_group_cached(map, trophy)` | Pre-fetched group lookup |
| `trophy_rarity_label(rarity)` | Convert int (0-3) to label |
| `is_dlc_trophy(trophy)` | Check if not base game |
| `in_set(value, the_set)` | Membership check |

### trophies/templatetags/markdown_filters.py

| Filter | Purpose |
|--------|---------|
| `render_markdown(text)` | Markdown to HTML via `ChecklistService.process_markdown()` with bleach sanitization |

## View Mixins

Defined in `trophies/mixins.py`:

| Mixin | Purpose | Behavior |
|-------|---------|----------|
| `PremiumRequiredMixin` | Enforce premium membership | Redirects non-premium to `beta_access_required` |
| `StaffRequiredMixin` | Staff gate for page views | Unauthenticated to login, non-staff to home |
| `StaffRequiredAPIMixin` | Staff gate for JSON APIs | Returns 401/403 JSON responses |
| `RecapSyncGateMixin` | Gate recap until sync complete | Shows gated template if no profile or not synced |
| `ProfileHotbarMixin` | Inject sync status into context | Adds `hotbar` dict with profile, sync progress, queue position |
| `BackgroundContextMixin` | Page-specific background image | Builds `image_urls` dict from Concept `bg_url` |

## Template Patterns

### Partial Templates

Reusable HTML fragments in `templates/*/partials/`:
- Included via `{% include "path/to/partial.html" with var=value %}`
- Used for: dashboard modules, challenge components, fundraiser sections, notification cards
- Naming convention: `partials/<feature>/<component>.html`

### AJAX Partial Pattern

Many list views support both full-page and AJAX partial rendering:
```python
def get(self, request, *args, **kwargs):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render(request, 'partial_template.html', context)
    return render(request, 'full_template.html', context)
```

Used by `InfiniteScroller` for paginated content loading.

## Related Docs

- [JS Utilities](js-utilities.md): ZoomScaler, InfiniteScroller, and other shared JS
- [Settings Overview](settings-overview.md): Theme and ad configuration
- [Dashboard](../features/dashboard.md): Module template pattern
