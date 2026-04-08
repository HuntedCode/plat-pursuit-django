# Share Images System

The share images system generates downloadable PNG cards that users can post on social media to show off their platinum trophies, badge progress, challenge completions, and monthly recaps. There are two rendering pipelines: a legacy Pillow-based renderer for notification-attached platinum images, and the primary Playwright-based renderer that screenshots HTML/CSS templates into pixel-perfect PNGs. All card types support landscape (1200x630, optimized for Discord/Twitter) and portrait (1080x1350, optimized for Instagram) formats.

## Architecture Overview

The rendering pipeline has three layers: data assembly, HTML generation, and PNG rendering.

**Data assembly** is handled by `ShareableDataService`, which collects all the metadata needed for a share card from Django models. For platinum cards, this includes game info, trophy stats, earn rate, rarity, badge XP earned, tier 1 badge progress, and the user's personal rating. The service computes historical totals (e.g., "Platinum #47" at the time of earning) rather than current totals, so cards remain accurate even when generated weeks after the fact. Each share card type (challenge, recap, badge) has its own data assembly logic in its respective view.

**HTML generation** uses Django's `render_to_string()` with dedicated share card templates. These templates use fully inline styles (no Tailwind, no external CSS) with hex colors for maximum Playwright rendering compatibility. All cards follow a unified design language: rich identity bar (avatar with glow border, Plus subscriber badge, username, card type label, "Platinum Pursuit" branding), colored-tint stat boxes, and normalized footers. Font stack is `'Inter', 'Poppins', system-ui, -apple-system, sans-serif`. Images reference `/api/v1/share-temp/<hash>` URLs or `/static/` paths.

**PNG rendering** uses Playwright (headless Chromium) via `playwright_renderer.py`. Before handing HTML to Chromium, the renderer inlines all external resources as base64 data URIs: fonts become embedded `@font-face` rules, images from the share temp directory and `/static/` are converted to `data:` URIs. This is necessary because `page.set_content()` runs in an `about:blank` origin with no file system access. The renderer runs Playwright in a dedicated daemon thread (`ThreadPoolExecutor` with `max_workers=1`) to keep its asyncio event loop isolated from Django's synchronous ORM.

**Image caching** is handled by `ShareImageCache`, which downloads external images (game covers, trophy icons, avatars) to a local `share_temp_images/` directory. Filenames are deterministic MD5 hashes of the source URL, so cached files persist across Gunicorn workers without shared state. An opportunistic cleanup runs with ~2% probability per fetch, deleting files older than 4 hours.

The legacy Pillow-based renderer (`ShareImageService`) still exists for the original notification-attached share images stored in S3 as `PlatinumShareImage` records. New share card types all use the Playwright pipeline.

## File Map

| File | Purpose |
|------|---------|
| `core/services/playwright_renderer.py` | Playwright PNG rendering: base64 embedding, font faces, theme CSS, dedicated thread execution |
| `core/services/share_image_cache.py` | Fetch and cache external images locally with deterministic filenames and opportunistic cleanup |
| `notifications/services/share_image_service.py` | Legacy Pillow-based renderer for notification platinum images |
| `notifications/services/shareable_data_service.py` | Centralized data collection for platinum share cards: metadata, badge XP, tier 1 progress, ratings |
| `api/shareable_views.py` | ShareableImageHTMLView, ShareableImagePNGView (EarnedTrophy-based, My Shareables page) |
| `api/notification_views.py` | Notification share image views: generate, retrieve, status, HTML preview, PNG download |
| `api/recap_views.py` | RecapShareImageHTMLView, RecapShareImagePNGView |
| `api/az_challenge_share_views.py` | AZChallengeShareHTMLView, AZChallengeSharePNGView |
| `api/calendar_challenge_share_views.py` | CalendarChallengeShareHTMLView, CalendarChallengeSharePNGView |
| `api/genre_challenge_share_views.py` | GenreChallengeShareHTMLView, GenreChallengeSharePNGView |
| `templates/partials/rate_before_download_modal.html` | Rating prompt modal shown before downloading if user hasn't rated |
| `trophies/themes.py` | GRADIENT_THEMES dictionary: background CSS for each selectable theme |
| `trophies/views/checklist_views.py` (MyShareablesView) | My Shareables hub page view |
| `notifications/models.py` (PlatinumShareImage) | S3-stored share images for notification-based generation |

## Data Model

### PlatinumShareImage

| Field | Type | Notes |
|-------|------|-------|
| `notification` | FK to Notification | CASCADE. The platinum notification this image was generated for |
| `format` | CharField | landscape (1200x630) or portrait (1080x1350) |
| `image` | ImageField | S3-stored PNG file |
| `created_at` | DateTimeField | Auto |

This model is used only by the legacy Pillow pipeline and the notification-based generation flow. The newer Playwright-based endpoints (My Shareables, challenges, recaps) return PNGs directly as HTTP responses without storing them.

### Share Temp Directory

Not a Django model. Local filesystem directory at `{BASE_DIR}/share_temp_images/`. Contains cached external images with deterministic filenames (MD5 hash of URL). Served via `/api/v1/share-temp/<filename>`. Files cleaned up after 4 hours.

## Key Flows

### Generating a Platinum Share Card (My Shareables / Playwright Pipeline)

1. User clicks download on My Shareables page or the platinum notification share button
2. Client requests `/api/v1/shareables/platinum/<earned_trophy_id>/png/?image_format=landscape&theme=default`
3. View calls `ShareableDataService.get_platinum_share_data(earned_trophy)` to collect metadata
4. `ShareImageCache.fetch_and_cache()` downloads external images (game cover, trophy icon, avatar) to local temp directory
5. `render_to_string()` generates the card HTML using the share card template
6. `playwright_renderer.render_png()` wraps the HTML in a full document:
   - Embeds fonts as base64 `@font-face` rules (cached after first build)
   - Builds theme-specific background CSS (gradient or game art with overlay)
   - Resolves all `/api/v1/share-temp/` and `/static/` URLs to base64 data URIs
   - Share-temp images are resized to 200px max for HTML size reduction
7. The full HTML is submitted to the Playwright thread pool
8. Playwright creates a page, sets the content, screenshots `.share-image-content` element
9. PNG bytes returned as an HTTP response with `Content-Disposition: attachment`

### Generating a Notification Share Image (Legacy Pipeline)

1. Client POSTs to `/api/v1/notifications/<id>/share-image/generate/`
2. `ShareImageService.generate_image()` creates the image using Pillow:
   - Creates gradient background pixel by pixel
   - Loads fonts (Poppins, Inter) with system font fallbacks
   - Fetches game cover and trophy icon via HTTP
   - Renders card layout (landscape or portrait) with rounded rectangles, stat grids, badges
3. Image saved as `PlatinumShareImage` record in S3
4. Subsequent requests retrieve the stored image without re-rendering

### Image Caching Flow (ShareImageCache)

1. `fetch_and_cache(url)` checks in-memory cache (30-minute TTL, per-worker)
2. If miss: checks filesystem with deterministic filename (`MD5(url).ext`)
3. If file exists on disk: touch to refresh mtime, update in-memory cache, return serve path
4. If not on disk: download from external URL, determine extension from Content-Type header, save to `share_temp_images/`
5. ~2% probability per fetch: run cleanup of files older than 4 hours

### Theme Application

Themes are applied at two levels depending on context:

**Server-side (PNG download via Playwright)**:
1. `_get_background_css()` resolves the theme gradient or game art overlay
2. `_get_banner_css()` resolves banner/header styles using `[data-element]` CSS selectors
3. Both are injected into the full HTML document's `<style>` block with `!important`

**Client-side (dashboard previews)**:
1. `_initShareCards()` fetches default-themed HTML via API, then applies themes by modifying the DOM directly via `applyTheme()`
2. Standard themes: sets `.share-image-content` background to the gradient CSS
3. Game art themes (`requiresGameImage`): composites a dark overlay with the game cover URL (captured from the API response's `game_image_base64` / `concept_bg_base64` fields)
4. Banner accent updated via `[data-element]` querySelector if theme provides `bannerBackground`
5. Game art themes are only shown in swatch grids for cards that set `data-supports-game-art="true"` (platinum card only, since it has game images available)

## API Endpoints

### Playwright-Based (Primary)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/shareables/platinum/<earned_trophy_id>/html/` | Yes | HTML preview for platinum card |
| GET | `/api/v1/shareables/platinum/<earned_trophy_id>/png/` | Yes | PNG download for platinum card |
| GET | `/api/v1/challenges/az/<challenge_id>/share/html/` | Yes | HTML preview for A-Z challenge card |
| GET | `/api/v1/challenges/az/<challenge_id>/share/png/` | Yes | PNG download for A-Z challenge card |
| GET | `/api/v1/challenges/calendar/<challenge_id>/share/html/` | Yes | HTML preview for Calendar challenge card |
| GET | `/api/v1/challenges/calendar/<challenge_id>/share/png/` | Yes | PNG download for Calendar challenge card |
| GET | `/api/v1/challenges/genre/<challenge_id>/share/html/` | Yes | HTML preview for Genre challenge card |
| GET | `/api/v1/challenges/genre/<challenge_id>/share/png/` | Yes | PNG download for Genre challenge card |
| GET | `/api/v1/recap/<year>/<month>/html/` | Yes | HTML preview for monthly recap card |
| GET | `/api/v1/recap/<year>/<month>/png/` | Yes | PNG download for monthly recap card |

### Legacy / Notification-Based

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/notifications/<id>/share-image/generate/` | Yes | Generate and store share images (Pillow) |
| GET | `/api/v1/notifications/<id>/share-image/<format_type>/` | Yes | Retrieve stored share image |
| GET | `/api/v1/notifications/<id>/share-image/status/` | Yes | Check which formats exist |
| GET | `/api/v1/notifications/<id>/share-image/html/` | Yes | HTML preview (Playwright) |
| GET | `/api/v1/notifications/<id>/share-image/png/` | Yes | PNG download (Playwright) |

### Infrastructure

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/share-temp/<filename>` | No | Serve cached temp images (for HTML preview) |

## Integration Points

- **SiteEvent tracking**: `recap_share_generate` tracked server-side when recap HTML is fetched. `recap_image_download` tracked client-side on download button click.
- **Theme system** (`trophies/themes.py`): `GRADIENT_THEMES` dictionary defines all available backgrounds. Share cards and the main site share the same theme definitions.
- **Font system**: Share cards use Poppins and Inter fonts from `static/fonts/`. The Playwright renderer embeds them as base64 `@font-face` rules (cached per process). Available weights: Inter Regular (400), SemiBold (600), Bold (700); Poppins Regular (400), SemiBold (600), Bold (700). The site's Google Fonts link (`base.html`) loads matching weights so browser previews and Playwright PNGs render consistently.
- **My Shareables page**: Centralized hub at `/my-shareables/` that lists all platinum trophies, challenges, and recaps with share card generation for each.

## Rate Before Download

When a user clicks "Download Image" on a platinum share card, the system checks whether they have rated the game. If they haven't (and haven't been prompted already this session), a modal appears prompting them to rate before downloading. This leverages the natural motivation of wanting a complete-looking share card: rated cards show personalized stats pills, while unrated cards show a "No Rating Yet!" badge.

### Flow

**My Shareables / Notifications** (ShareImageManager):
1. `ShareImageManager.generateAndDownload()` checks `this.ratingData.hasRating` (populated from the HTML API response during preview rendering)
2. If unrated and not yet prompted: opens the `#rate-before-download-modal` dialog
3. User can "Rate and Download" (submits rating via `/api/v1/reviews/<concept_id>/group/default/rate/`, refreshes preview, then downloads) or "Skip, just download"
4. If already rated or already prompted: download proceeds immediately
5. Prompted IDs are tracked in `ShareImageManager._promptedIds` (class-level `Set`) to avoid nagging

**Dashboard** (DashboardManager):
1. When the platinum card HTML is fetched, `concept_id`, `has_rating`, `is_shovelware`, and `playtime` are captured from the API response and stored on the preview element's dataset
2. Download button click checks these data attributes before proceeding
3. If unrated and not shovelware: `_showRatingPrompt()` opens the same `#rate-before-download-modal`
4. After rating submission (or skip), the PNG download URL is triggered via `window.location.href`
5. Prompted IDs tracked in a local `Set` per `_initShareCards` call, scoped to the session

### Key Files

| File | Purpose |
|------|---------|
| `templates/partials/rate_before_download_modal.html` | Rating prompt modal with all 5 rating fields |
| `static/js/share-image.js` | Interception logic in base `ShareImageManager` class |
| `static/js/shareable-manager.js` | Passes `conceptId` from data attributes to manager |
| `static/js/dashboard.js` | `_showRatingPrompt()` method for dashboard platinum card downloads |
| `api/shareable_views.py` | Returns `has_rating`, `concept_id`, `playtime`, `is_shovelware` in HTML API response |
| `api/notification_views.py` | Same metadata in notification HTML API response |

### Data Flow

Rating metadata flows through two paths depending on the page:
- **My Shareables / Notifications**: `data-concept-id` on share card elements provides `conceptId` at construction time. `has_rating` returned in HTML API response during `fetchCardHTML()`.
- **Dashboard**: The platinum card HTML API response includes `has_rating`, `concept_id`, `is_shovelware`, and `playtime`. These are stored as `data-*` attributes on the preview element after fetch, then read by the download button handler.

In both cases, the JS knows the rating status before the user clicks download, with no extra API call.

## Gotchas and Pitfalls

- **Playwright thread isolation**: Playwright starts an asyncio event loop, which conflicts with Django's `SynchronousOnlyOperation` guard. All Playwright interaction must happen in the dedicated thread. The `_executor` ThreadPoolExecutor with `max_workers=1` serializes renders and keeps the event loop isolated.
- **30-second timeout**: `future.result(timeout=30)` means renders that take longer will raise `TimeoutError`. Cards with many images (A-Z challenge with 26 game icons) are mitigated by resizing share-temp images to 200px before embedding.
- **base64 HTML size**: Embedding images as data URIs can produce massive HTML strings. The `resize_images=True` flag in `_resolve_urls()` compresses share-temp images (external game icons/avatars) to keep HTML under ~1MB. Static assets (fonts, logos) are not resized.
- **Deterministic cache filenames**: `MD5(url)` means the same external URL always maps to the same file. This is intentional for cross-worker cache sharing, but it also means a URL whose content changes (e.g., profile avatar updates) will serve the stale cached version until cleanup runs.
- **Portrait vs. landscape game art positioning**: Portrait cards use `background-position: center top` so wide game art images show their upper portion (where logos and characters typically appear). Landscape cards use `center`.
- **Legacy Pillow renderer limitations**: `_wrap_text()` is a naive implementation that truncates at 40 characters. The Pillow renderer also creates gradients pixel by pixel, which is slow for large images. New card types should always use the Playwright pipeline.
- **Font loading is cached per process**: `_cached_font_faces` is a module-level global. Changing fonts requires a process restart (Gunicorn reload).
- **Rate prompt is session-scoped**: `ShareImageManager._promptedIds` is a class-level `Set` that resets on page navigation. This is intentional: users should not be nagged across sessions, but a fresh page load gives one prompt opportunity per card.
- **Identity bar `is_plus` must be passed by every view**: All share card HTML views pass `is_plus` from the profile to the template context. For `shareable_views.py`, the `profile` parameter on `_build_template_context()` is optional (defaults to `None`) to avoid breaking the notification view which calls it without a profile.
- **Dashboard theme switching is client-side**: The share card HTML endpoints do NOT apply themes. Dashboard previews fetch default-styled HTML, then `applyTheme()` modifies the DOM. Only the PNG endpoint applies themes server-side (via Playwright CSS injection).
- **Game art theme swatches update asynchronously**: Game art swatch buttons are created with the fallback gradient background. After the API response returns game image URLs, `updateGameArtSwatches()` updates them with the actual composited game cover. This avoids blocking swatch grid rendering on the API call.

## Related Docs

- [Mobile App](../guides/mobile-app.md): Mobile app may consume share image endpoints
- [Dashboard](dashboard.md): Dashboard may include shareable statistics modules
