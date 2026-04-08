# SEO & Meta Tags

The SEO system provides dynamic meta tags, structured data (JSON-LD), sitemaps, and robots directives across all pages. Built on Django's template block system with no external packages.

## Architecture Overview

All SEO infrastructure lives in `templates/base.html` using template blocks that child templates can override. Views pass context variables (`seo_description`, `seo_title`) that automatically populate meta description, Open Graph, and Twitter Card tags. JSON-LD structured data is generated via custom template tags in `core/templatetags/seo_tags.py`.

The design prioritizes DRY: views set a single `seo_description` string and it flows into three different meta tags (meta description, OG description, Twitter description). Individual blocks can still be overridden when OG needs to differ from the meta tag.

## File Map

| File | Purpose |
|------|---------|
| `templates/base.html` | All SEO meta tag blocks, JSON-LD integration, favicon links |
| `core/templatetags/seo_tags.py` | JSON-LD template tags (Organization, WebSite, BreadcrumbList, VideoGame, ProfilePage) |
| `core/sitemaps.py` | Sitemap classes for all content types |
| `plat_pursuit/urls.py` | Sitemap registration at `/sitemap.xml` |
| `static/robots.txt` | Robots directives (served via `RobotsTxtView`) |

## SEO Block System (base.html)

### Available Blocks

| Block | Default Behavior | When to Override |
|-------|-----------------|-----------------|
| `title` | Falls back to `{{ title }}` context var or "Platinum Pursuit". Auto-appends " - Platinum Pursuit" suffix. | Always set for named pages |
| `meta_description` | Uses `{{ seo_description }}` context var, falls back to site tagline | Set via `seo_description` in views, or override block for static pages |
| `robots` | `index, follow` | Override with `noindex, nofollow` for auth/personal/edit pages |
| `canonical_url` | `{{ request.build_absolute_uri }}` | Rarely needs override |
| `og_title` | Uses `{{ seo_title }}` or `{{ title }}` context var | Only if OG title should differ from page title |
| `og_description` | Uses `{{ seo_description }}` context var | Only if OG description should differ from meta description |
| `og_type` | `website` | Override: `profile` for profile pages, `article` for guides |
| `og_image` | Falls back to site logo | Override for pages with dynamic images |
| `twitter_card_type` | `summary` | Use `summary_large_image` for pages with large thumbnails |
| `twitter_title` | Same as OG title | Rarely needs separate override |
| `twitter_description` | Same as OG description | Rarely needs separate override |
| `twitter_image` | Same as OG image | Rarely needs separate override |

### Context Variable Pattern

Instead of overriding multiple blocks per page, views set a single context variable:

```python
# In get_context_data():
context['seo_description'] = f"{game.title_name} on {game.platforms_display}. ..."
```

This automatically populates:
- `<meta name="description">`
- `<meta property="og:description">`
- `<meta name="twitter:description">`

Similarly, `seo_title` populates OG and Twitter title tags.

## JSON-LD Structured Data

### Template Tags (`core/templatetags/seo_tags.py`)

Load with `{% load seo_tags %}`.

| Tag | Usage | Output |
|-----|-------|--------|
| `{% jsonld_organization %}` | All pages (in base.html) | Organization schema with name, URL, logo |
| `{% jsonld_website request %}` | Homepage only | WebSite schema with SearchAction |
| `{% jsonld_breadcrumbs breadcrumb request %}` | Pages with breadcrumb context (in base.html) | BreadcrumbList from existing breadcrumb data |
| `{% jsonld_game game concept request %}` | Game detail page | VideoGame schema with platforms, publisher, genres |
| `{% jsonld_profile profile request %}` | Profile detail page | ProfilePage schema with username, avatar |

### Adding a New Schema

1. Add a new `@register.simple_tag` function in `seo_tags.py`
2. Build the schema dict and return `_render_jsonld(data)`
3. Add `{% load seo_tags %}` to the template (if not already loaded)
4. Call the tag in `{% block extra_head %}` or inline

## Sitemaps (`core/sitemaps.py`)

| Class | Content | Priority | Frequency |
|-------|---------|----------|-----------|
| `StaticViewSitemap` | Homepage, about, privacy, terms, contact, browse pages | 0.8 | weekly |
| `GameSitemap` | All games with `np_communication_id` | 0.6 | weekly |
| `ProfileSitemap` | All profiles with `psn_username` | 0.5 | daily |
| `BadgeSitemap` | Tier-1 active badges | 0.6 | weekly |
| `GuideSitemap` | Published checklists/guides | 0.5 | weekly |
| `GameListSitemap` | Public game lists | 0.4 | weekly |
| `ChallengeSitemap` | Non-deleted challenges (A-Z, Calendar, Genre) | 0.4 | daily |

Django auto-generates a sitemap index when multiple sections exist. Pagination is handled automatically (50,000 URLs per file).

### Adding a New Sitemap

1. Create a new `Sitemap` subclass in `core/sitemaps.py`
2. Define `items()`, `location()`, and optionally `lastmod()`
3. Register it in the `sitemaps` dict in `plat_pursuit/urls.py`

## Key Flows

### Page Renders with SEO Data

1. View's `get_context_data()` sets `seo_description` (and optionally `seo_title`)
2. `base.html` renders meta tags using the context variables
3. JSON-LD Organization and BreadcrumbList render automatically (if breadcrumb context exists)
4. Page-specific JSON-LD renders via `{% block extra_head %}`

### Adding SEO to a New Page

1. Set `{% block title %}Page Name{% endblock %}` in the template
2. Add `context['seo_description'] = "..."` in the view's `get_context_data()`
3. If auth-required or personal: add `{% block robots %}noindex, nofollow{% endblock %}`
4. If the page has a dynamic image: override `{% block og_image %}`

## Gotchas and Pitfalls

- **Title double-suffix**: `base.html` auto-appends " - Platinum Pursuit" to `{% block title %}`. Never include "- Platinum Pursuit" inside the block content or it will appear twice.
- **seo_description max length**: Google typically shows 155-160 characters for meta descriptions. Keep `seo_description` values concise.
- **OG image requirements**: Facebook recommends 1200x630px images. The default logo.png fallback is small (128x128). For best social sharing, override `og_image` with a larger image.
- **noindex on personal pages**: Auth-required pages (dashboard, my_*, settings) have `noindex` set. If you create a new personal page, add the robots block.
- **Breadcrumb structure**: JSON-LD breadcrumbs expect `[{'text': 'Name', 'url': '/path/'}, ...]`. The last item should omit `url` (it auto-uses the current page URL).
- **Sitemap model querysets**: Don't add `[:N]` limits to sitemap querysets. Django handles pagination natively.
- **Template tag loading**: `seo_tags` is loaded in `base.html`, but child templates that use page-specific tags (like `jsonld_game`) need to load it again: `{% load seo_tags %}`.

## Related Docs

- [Data Model](../architecture/data-model.md): Game, Profile, Badge, Challenge models (Checklist tables retained in schema but the system was retired)
- [JS Utilities](js-utilities.md): Frontend utilities
