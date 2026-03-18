# Profile Cards & Forum Signatures

Shareable profile card images showcasing a user's trophy hunting identity. Two output formats: social media cards (downloadable PNGs) and forum signatures (auto-updating public URLs).

## Overview

The profile card system extends the existing share card infrastructure (Playwright-based PNG rendering, ShareImageManager JS base class, theme system) with two new capabilities:

1. **Social Media Card**: Downloadable PNG for Discord/Twitter (1200x630 landscape) and Instagram (1080x1350 portrait). Auth-required, on-demand Playwright rendering.
2. **Forum Signature**: Compact banner image (728x120) with a public, auto-updating URL. Available as PNG (maximum compatibility) and SVG (subtle CSS animations). Pre-rendered to disk to keep Playwright off the public request path.

## Architecture

```
ProfileCardDataService  (collects all data)
        |
        +---> Social Card (auth, on-demand Playwright)
        |       ProfileCardHTMLView -> JS preview
        |       ProfileCardPNGView  -> Playwright render -> download
        |
        +---> Forum Sig PNG (pre-rendered, public)
        |       render_sig_png() -> profile_sigs/<token>.png
        |       /sig/<token>.png -> FileResponse
        |
        +---> Forum Sig SVG (pre-rendered, public)
                render_sig_svg() -> profile_sigs/<token>.svg
                /sig/<token>.svg -> FileResponse
```

## Key Files

### Models
- `trophies/models.py` : `ProfileCardSettings` (OneToOneField to Profile)
  - `public_sig_enabled`: Toggle for public URL
  - `public_sig_token`: UUID for URL (regeneratable)
  - `card_theme`: Theme key
  - `sig_last_rendered`, `sig_render_hash`: Change-detection cache

### Services
- `core/services/profile_card_service.py` : `ProfileCardDataService`
  - `get_profile_card_data(profile)`: Gathers all stats, badge, title, XP rank, etc.
  - `compute_data_hash(data)`: MD5 for change detection
- `core/services/profile_card_renderer.py` : Pre-rendering pipeline
  - `render_sig_png(profile)`: Playwright-based PNG generation
  - `render_sig_svg(profile)`: Template-based SVG generation
  - `render_all_sigs(profile)`: Both formats
  - `cleanup_orphaned_sigs()`: Remove stale files

### API Views (`api/profile_card_views.py`)
- `ProfileCardHTMLView`: `GET /api/v1/profile-card/html/` (auth, 60/min)
- `ProfileCardPNGView`: `GET /api/v1/profile-card/png/` (auth, 20/min)
- `ProfileCardSettingsView`: `GET/POST /api/v1/profile-card/settings/` (auth)
- `ProfileCardRegenerateTokenView`: `POST /api/v1/profile-card/regenerate-token/` (auth)
- `SetDisplayedBadgeView`: `POST /api/v1/badges/displayed/` (auth)
- `serve_profile_sig`: `GET /sig/<token>.(png|svg)` (public, 120/min by IP)

### Templates
- `templates/shareables/partials/profile_card_landscape.html` (1200x630)
- `templates/shareables/partials/profile_card_portrait.html` (1080x1350)
- `templates/shareables/partials/profile_sig_card.html` (728x120 for Playwright)
- `templates/shareables/partials/profile_sig_card.svg` (728x120 self-contained SVG)
- `templates/shareables/partials/profile_card_tab.html` (My Shareables UI)
- `templates/trophies/partials/dashboard/badge_showcase.html` (dashboard module)

### JavaScript
- `static/js/profile-card-share.js` : `ProfileCardShareManager extends ShareImageManager`
  - Social card: format toggle, theme, preview, download (inherited from base)
  - Forum sig: enable/disable toggle, URL copy (URL/BBCode/HTML/Markdown), token regeneration
  - `PlatPursuit.setBadgeDisplayed(badgeId)`: Badge selection for dashboard module

### Management Commands
- `python manage.py render_profile_sigs` : Batch render sigs
  - `--profile=username` : Single profile
  - `--force` : Re-render even if unchanged
  - `--svg-only` : Skip Playwright (faster)
  - `--cleanup` : Remove orphaned files

## Data Flow

### Social Card Download
1. User opens My Shareables > Profile Card tab
2. JS creates `ProfileCardShareManager`, calls `renderShareSection()`
3. User selects format/theme, preview loads via `GET /api/v1/profile-card/html/`
4. Download click triggers `GET /api/v1/profile-card/png/` with Playwright render
5. Browser downloads the PNG

### Forum Sig Lifecycle
1. User enables sig toggle on My Shareables > Profile Card > Forum Signature
2. `POST /api/v1/profile-card/settings/` with `public_sig_enabled: true`
3. Server calls `render_all_sigs()` to generate initial PNG + SVG
4. User copies the public URL (e.g., `https://platpursuit.com/sig/<token>.png`)
5. On each sync completion, `render_sig_svg()` is called to update the SVG
6. PNG is updated by the nightly `render_profile_sigs` batch command
7. Public requests to `/sig/<token>.(png|svg)` serve pre-rendered files from disk

### Badge Selection
1. User opens Dashboard > Share tab > Badge Showcase module
2. Clicks a badge to feature it
3. `POST /api/v1/badges/displayed/` clears old `is_displayed`, sets new one
4. Dashboard cache invalidated, profile card data reflects new badge

### Dashboard Integration
The Dashboard "Share & Export" tab provides two modules for profile cards:
- **Badge Showcase**: Select a featured badge (filtered to badges with custom artwork only). Selecting a badge dispatches a `platpursuit:badge-changed` custom event.
- **Profile Card Preview**: Scaled-down landscape card preview with inline theme picker (swatch grid), full-size modal on click, and download button. Fetches HTML via `/api/v1/profile-card/html/` client-side. Listens for `platpursuit:badge-changed` to refresh the preview when the featured badge changes. Theme selection saves via `POST /api/v1/profile-card/settings/` and applies to the preview immediately.

Template: `templates/trophies/partials/dashboard/profile_card_preview.html`
JS init: `_initProfileCardPreview()` in `static/js/dashboard.js` (registered via `registerModuleInit`)

## Access Control

- **Social card**: Auth required (IsAuthenticated). Rate limited per user.
- **Forum sig**: Public (no auth). Rate limited per IP (120/min). Only serves files if `public_sig_enabled=True`.
- **Theme customization**: Free users get default theme. Premium users get full theme selection.
- **Sig URL security**: UUID tokens (non-enumerable). Users can regenerate to invalidate old URLs.

## Caching Strategy

- **Forum sig PNG**: Pre-rendered to `profile_sigs/<token>.png`. Served with `Cache-Control: public, max-age=3600`. Re-rendered by batch command or on manual trigger.
- **Forum sig SVG**: Pre-rendered to `profile_sigs/<token>.svg`. Re-rendered on sync completion (cheap: no Playwright). On-demand fallback if file missing.
- **Social card**: No caching. Each download triggers a fresh Playwright render.
- **Change detection**: `sig_render_hash` stores MD5 of data dict. Skips re-render when unchanged.

## Edge Cases

| Scenario | Behavior |
|---|---|
| No avatar | PS trophy SVG placeholder |
| No platinums | Shows total trophies instead |
| No badges earned | Shows completion stats in badge slot |
| No title equipped | Title line omitted, username gets more space |
| No displayed badge | Auto-selects highest-tier earned badge |
| No leaderboard rank | Rank display omitted |
| User disables sig | `/sig/<token>` returns 404 |
| Sig file missing but enabled | SVG: on-demand render. PNG: 404 (wait for batch). |
| Token regeneration | Old files deleted, new files rendered, old URLs break |

## Gotchas and Pitfalls

1. **Playwright single-threaded**: The Playwright worker is `max_workers=1`. Forum sig PNGs are pre-rendered (not on public requests) to avoid overwhelming it. Only SVG (no Playwright) is rendered on sync completion.

2. **SVG XSS**: User data (psn_username, displayed_title) is embedded in SVG. Django's template auto-escaping handles this, but be cautious with any raw/safe filter usage in the SVG template.

3. **ProfileCardSettings vs DashboardConfig**: Both are OneToOneField to Profile with `primary_key=True`. They serve different purposes. Card settings is for the profile card feature; dashboard config is for module layout.

4. **Badge image URLs**: Badge images use `object-contain` (not `object-cover`) because badges have transparent backgrounds and custom shapes. The `get_badge_layers()` method handles tier-based inheritance.

5. **`profile_sigs/` directory**: Added to `.gitignore`. Must exist on the server. Created automatically by the renderer on first use.

6. **Concept.absorb() not affected**: `ProfileCardSettings` has no FK to Concept, so no absorb update is needed.
