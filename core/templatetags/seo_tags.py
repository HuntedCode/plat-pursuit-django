"""
Template tags for SEO structured data (JSON-LD / Schema.org).

Usage:
    {% load seo_tags %}
    {% jsonld_organization %}
    {% jsonld_website request %}
    {% jsonld_breadcrumbs breadcrumb request %}
    {% jsonld_game game concept request %}
    {% jsonld_profile profile request %}
"""

import json
from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()


def _get_site_url():
    return getattr(settings, 'SITE_URL', 'https://platpursuit.com')


def _render_jsonld(data):
    """Render a Python dict as a JSON-LD script tag."""
    json_str = json.dumps(data, ensure_ascii=False, default=str)
    return mark_safe(f'<script type="application/ld+json">{json_str}</script>')


@register.simple_tag
def jsonld_organization():
    """Site-wide Organization schema."""
    return _render_jsonld({
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Platinum Pursuit",
        "url": _get_site_url(),
        "logo": f"{_get_site_url()}/static/images/logo.png",
    })


@register.simple_tag
def jsonld_website(request):
    """Homepage WebSite schema with SearchAction."""
    return _render_jsonld({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Platinum Pursuit",
        "url": _get_site_url(),
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{_get_site_url()}/search/?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    })


@register.simple_tag
def jsonld_breadcrumbs(breadcrumb, request):
    """BreadcrumbList schema from the existing breadcrumb context variable.

    Expects breadcrumb to be a list of dicts with 'text' and optional 'url' keys.
    """
    if not breadcrumb:
        return ''

    base_url = f"{request.scheme}://{request.get_host()}"
    items = []
    for i, crumb in enumerate(breadcrumb, start=1):
        item = {
            "@type": "ListItem",
            "position": i,
            "name": crumb.get('text', ''),
        }
        url = crumb.get('url')
        if url:
            # Handle both absolute and relative URLs
            if url.startswith('http'):
                item["item"] = url
            else:
                item["item"] = f"{base_url}{url}"
        elif i == len(breadcrumb):
            # Last item: use the current page URL
            item["item"] = request.build_absolute_uri()
        items.append(item)

    return _render_jsonld({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": items,
    })


@register.simple_tag
def jsonld_game(game, concept, request):
    """VideoGame schema for game detail pages."""
    if not game or not getattr(game, 'title_name', None):
        return ''
    base_url = f"{request.scheme}://{request.get_host()}"
    data = {
        "@context": "https://schema.org",
        "@type": "VideoGame",
        "name": game.title_name,
        "url": request.build_absolute_uri(),
    }

    # Image
    image_url = game.image_url
    if image_url:
        if not image_url.startswith('http'):
            image_url = f"{base_url}{image_url}"
        data["image"] = image_url

    # Platforms
    if game.title_platform:
        platform_map = {
            'PS5': 'PlayStation 5',
            'PS4': 'PlayStation 4',
            'PS3': 'PlayStation 3',
            'PSVITA': 'PlayStation Vita',
            'PSVR': 'PlayStation VR',
            'PSVR2': 'PlayStation VR2',
        }
        data["gamePlatform"] = [
            platform_map.get(p, p) for p in game.title_platform
        ]

    # Concept-level data
    if concept:
        if concept.publisher_name:
            data["publisher"] = {
                "@type": "Organization",
                "name": concept.publisher_name,
            }

        # Developer(s) as author
        try:
            developers = [
                cc.company.name
                for cc in concept.concept_companies.all()
                if cc.is_developer
            ]
            if developers:
                data["author"] = [
                    {"@type": "Organization", "name": name}
                    for name in developers
                ]
        except Exception:
            pass

        # Prefer IGDB genres, fall back to PSN genres
        genres = concept.igdb_genres or concept.genres
        if genres:
            data["genre"] = genres

        if concept.release_date:
            data["datePublished"] = concept.release_date.strftime('%Y-%m-%d')

        # Time to complete (from IGDB match). Gate on is_trusted — pending
        # or rejected matches still have the fields populated but the data
        # isn't reviewed and shouldn't leak into public SEO metadata.
        try:
            igdb_match = concept.igdb_match
            if igdb_match and igdb_match.is_trusted and igdb_match.time_to_beat_completely:
                hours = igdb_match.time_to_beat_completely // 3600
                minutes = (igdb_match.time_to_beat_completely % 3600) // 60
                data["timeRequired"] = f"PT{hours}H{minutes}M"
        except Exception:
            pass

    return _render_jsonld(data)


@register.simple_tag
def jsonld_roadmap(roadmap, game, concept, request, contributors=None):
    """HowTo + VideoGame combined schema for a trophy roadmap detail page.

    Roadmaps are instructional content (numbered steps + optional time
    estimates + author attribution), which maps cleanly onto schema.org's
    `HowTo` type. We pair it with a referenced `VideoGame` so the guide
    is anchored to the game it covers — improves rich-snippet eligibility
    and lets the panel show game metadata alongside the steps.

    Inputs are forgiving — missing optional fields are silently dropped.
    Always returns a valid schema dict with at least name + steps.
    """
    if not roadmap or not game:
        return ''

    base_url = f"{request.scheme}://{request.get_host()}"
    page_url = request.build_absolute_uri()
    game_title = game.title_name or ''
    group_name = ''
    try:
        group_name = roadmap.concept_trophy_group.display_name or ''
    except AttributeError:
        pass

    # Resolve the cover image for SEO use (same chain as the visible
    # game cover in the header).
    image_url = getattr(game, 'display_image_url', None) or ''
    if image_url and not image_url.startswith('http'):
        image_url = f"{base_url}{image_url}"

    # Step list → HowToStep array. Each step's anchor URL points back
    # at the page with the `#step-N` fragment so a search result can
    # deep-link to a specific step.
    steps_data = []
    try:
        for idx, step in enumerate(roadmap.steps.all(), start=1):
            step_obj = {
                "@type": "HowToStep",
                "position": idx,
                "name": (step.title or f"Step {idx}")[:200],
                "url": f"{page_url}#step-{step.id}",
            }
            description = (step.description or '').strip()
            if description:
                # Strip markdown-ish syntax for the schema text — search
                # crawlers prefer plain prose. Truncate to keep payload
                # compact; full text is on the page itself.
                step_obj["text"] = description[:500]
            steps_data.append(step_obj)
    except AttributeError:
        pass

    data = {
        "@context": "https://schema.org",
        "@type": "HowTo",
        # The display name should describe what the reader will achieve.
        "name": f"How to platinum {game_title}" + (
            f" ({group_name})" if group_name and group_name.lower() != 'base game' else ''
        ),
        "url": page_url,
        "description": _make_roadmap_description(roadmap, game, group_name),
    }
    if image_url:
        data["image"] = image_url
    if steps_data:
        data["step"] = steps_data

    # Total time — converts roadmap.estimated_hours into an ISO 8601
    # duration. PT24H, PT5H etc. Only emit when present.
    estimated_hours = getattr(roadmap, 'estimated_hours', None)
    if estimated_hours:
        try:
            data["totalTime"] = f"PT{int(estimated_hours)}H"
        except (TypeError, ValueError):
            pass

    # Difficulty (if author set it). schema.org doesn't have a strict
    # vocabulary for HowTo difficulty so we use a Text value.
    difficulty = getattr(roadmap, 'difficulty', None)
    if difficulty:
        data["proficiencyLevel"] = str(difficulty)

    # Author attribution — prefer explicit contributors list (a list of
    # display names from the `roadmap_authors` template tag); fall back
    # to the curated PlatPursuit team string if no contributors set.
    if contributors:
        names = [c for c in contributors if c]
        if names:
            data["author"] = [
                {"@type": "Person", "name": name} for name in names
            ]
    else:
        data["author"] = {
            "@type": "Organization",
            "name": "PlatPursuit Roadmap Team",
        }

    # `about` references the game itself — search engines use this to
    # link the guide back to the underlying VideoGame entity.
    if game_title:
        about = {
            "@type": "VideoGame",
            "name": game_title,
        }
        if concept and getattr(concept, 'publisher_name', None):
            about["publisher"] = {
                "@type": "Organization",
                "name": concept.publisher_name,
            }
        if concept and getattr(concept, 'release_date', None):
            about["datePublished"] = concept.release_date.strftime('%Y-%m-%d')
        if image_url:
            about["image"] = image_url
        data["about"] = about

    return _render_jsonld(data)


def _make_roadmap_description(roadmap, game, group_name):
    """Build a SEO-friendly description for the HowTo schema.

    Mirrors the meta description the page emits so search-result snippets
    and rich-snippet panels read consistently.
    """
    # Iterate the prefetched managers so the counts work even when the
    # caller (e.g. ?preview=true) has overlaid the cache with branch
    # objects. Calling .count() would clone the queryset and run a DB
    # COUNT — accurate against live state, but misleading in preview and
    # historically a 500 source while the overlay still held raw lists.
    try:
        step_count = sum(1 for _ in roadmap.steps.all())
        guide_count = sum(1 for _ in roadmap.trophy_guides.all())
    except (AttributeError, TypeError):
        step_count = 0
        guide_count = 0
    parts = [f"Complete trophy guide for {game.title_name}"]
    if group_name and group_name.lower() != 'base game':
        parts[0] += f" ({group_name})"
    parts.append('.')
    if step_count:
        parts.append(
            f" {step_count} step{'s' if step_count != 1 else ''}"
        )
        if guide_count:
            parts.append(
                f", {guide_count} trophy guide{'s' if guide_count != 1 else ''}."
            )
        else:
            parts.append('.')
    hours = getattr(roadmap, 'estimated_hours', None)
    if hours:
        parts.append(f" Estimated time: {int(hours)} hours.")
    difficulty = getattr(roadmap, 'difficulty', None)
    if difficulty:
        parts.append(f" Difficulty: {difficulty}.")
    return ''.join(parts)


@register.simple_tag
def jsonld_video(youtube_url, name, description, request, channel_name=''):
    """VideoObject schema for an embedded YouTube clip.

    Adding this lets search engines surface the embedded "Play Along"
    video as a rich-snippet card on the roadmap result. Skipped if no
    URL or if we can't parse the YouTube ID — better to omit the
    schema than emit a malformed one.
    """
    if not youtube_url:
        return ''
    video_id = _extract_youtube_id(youtube_url)
    if not video_id:
        return ''

    base_url = f"{request.scheme}://{request.get_host()}"
    data = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": name or 'Trophy Guide Video',
        "description": description or name or 'Trophy guide video walkthrough.',
        "thumbnailUrl": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "uploadDate": "2020-01-01",  # placeholder — YouTube oEmbed doesn't expose this on the cached side
        "contentUrl": youtube_url,
        "embedUrl": f"https://www.youtube.com/embed/{video_id}",
    }
    if channel_name:
        data["author"] = {
            "@type": "Person",
            "name": channel_name,
        }
    return _render_jsonld(data)


def _extract_youtube_id(url):
    """Pull the video id from a YouTube URL. Handles the three common
    formats: youtu.be/X, youtube.com/watch?v=X, youtube.com/embed/X.
    Returns None on anything we can't confidently parse.
    """
    if not url:
        return None
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if host.endswith('youtu.be'):
            return parsed.path.lstrip('/').split('/')[0] or None
        if 'youtube' in host:
            if parsed.path.startswith('/embed/'):
                return parsed.path.split('/embed/', 1)[1].split('/')[0] or None
            qs = parse_qs(parsed.query or '')
            v = qs.get('v', [None])[0]
            return v or None
    except Exception:
        return None
    return None


@register.simple_tag
def jsonld_profile(profile, request):
    """ProfilePage schema for profile detail pages."""
    base_url = f"{request.scheme}://{request.get_host()}"
    data = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "url": request.build_absolute_uri(),
        "mainEntity": {
            "@type": "Person",
            "name": profile.display_psn_username,
        },
    }

    avatar_url = profile.avatar_url
    if avatar_url:
        if not avatar_url.startswith('http'):
            avatar_url = f"{base_url}{avatar_url}"
        data["mainEntity"]["image"] = avatar_url

    return _render_jsonld(data)
