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

        if concept.genres:
            data["genre"] = concept.genres

        if concept.release_date:
            data["datePublished"] = concept.release_date.strftime('%Y-%m-%d')

    return _render_jsonld(data)


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
