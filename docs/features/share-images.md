# Share Images System

The share images system generates downloadable PNG cards that users can post on social media to show off their platinum trophies, badge progress, challenge completions, and monthly recaps. There are two rendering pipelines: a legacy Pillow-based renderer for notification-attached platinum images, and the primary Playwright-based renderer that screenshots HTML/CSS templates into pixel-perfect PNGs. All card types support landscape (1200x630, optimized for Discord/Twitter) and portrait (1080x1350, optimized for Instagram) formats.

## Architecture Overview

The rendering pipeline has three layers: data assembly, HTML generation, and PNG rendering.

**Data assembly** is handled by `ShareableDataService`, which collects all the metadata needed for a share card from Django models. For platinum cards, this includes game info, trophy stats, earn rate, rarity, badge XP earned, tier 1 badge progress, and the user's personal rating. The service computes historical totals (e.g., "Platinum #47" at the time of earning) rather than current totals, so cards remain accurate even when generated weeks after the fact. Each share card type (challenge, recap, badge) has its own data assembly logic in its respective view.

**HTML generation** uses Django's `render_to_string()` with dedicated share card templates. These templates use the same Tailwind classes and component patterns as the main site but are self-contained: no external CSS dependencies, no JavaScript. Images reference `/api/v1/share-temp/<hash>` URLs or `/static/` paths.

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

1. User selects a theme (default, gradient variants, or game art themes)
2. `_get_background_css()` resolves the theme:
   - Standard themes: CSS gradient background
   - Game art themes (`requires_game_image=True`): embeds game cover or concept background as a base64 data URI with a dark overlay
3. `_get_banner_css()` resolves the banner/header element styles per theme
4. Both are injected into the full HTML document's `<style>` block

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
- **Font system**: Share cards use the same Poppins and Inter fonts from `static/fonts/`. The Playwright renderer embeds them as base64 to avoid file system access issues.
- **My Shareables page**: Centralized hub at `/my-shareables/` that lists all platinum trophies, challenges, and recaps with share card generation for each.

## Gotchas and Pitfalls

- **Playwright thread isolation**: Playwright starts an asyncio event loop, which conflicts with Django's `SynchronousOnlyOperation` guard. All Playwright interaction must happen in the dedicated thread. The `_executor` ThreadPoolExecutor with `max_workers=1` serializes renders and keeps the event loop isolated.
- **30-second timeout**: `future.result(timeout=30)` means renders that take longer will raise `TimeoutError`. Cards with many images (A-Z challenge with 26 game icons) are mitigated by resizing share-temp images to 200px before embedding.
- **base64 HTML size**: Embedding images as data URIs can produce massive HTML strings. The `resize_images=True` flag in `_resolve_urls()` compresses share-temp images (external game icons/avatars) to keep HTML under ~1MB. Static assets (fonts, logos) are not resized.
- **Deterministic cache filenames**: `MD5(url)` means the same external URL always maps to the same file. This is intentional for cross-worker cache sharing, but it also means a URL whose content changes (e.g., profile avatar updates) will serve the stale cached version until cleanup runs.
- **Portrait vs. landscape game art positioning**: Portrait cards use `background-position: center top` so wide game art images show their upper portion (where logos and characters typically appear). Landscape cards use `center`.
- **Legacy Pillow renderer limitations**: `_wrap_text()` is a naive implementation that truncates at 40 characters. The Pillow renderer also creates gradients pixel by pixel, which is slow for large images. New card types should always use the Playwright pipeline.
- **Font loading is cached per process**: `_cached_font_faces` is a module-level global. Changing fonts requires a process restart (Gunicorn reload).

## Related Docs

- [Mobile App](../mobile-app.md): Mobile app may consume share image endpoints
- [Dashboard](../dashboard.md): Dashboard may include shareable statistics modules
