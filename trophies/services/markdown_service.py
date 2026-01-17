"""
Markdown rendering service for guide content.

This service handles:
- Converting markdown to HTML
- Processing trophy mentions [trophy:id]
- Processing spoiler tags ||text||
- Embedding YouTube videos
- Caching rendered content
"""

import logging
import markdown
from django.core.cache import cache
from django.utils.html import escape
from django.utils.safestring import mark_safe

from trophies.constants import RENDER_CACHE_TIMEOUT, TROPHY_PATTERN, SPOILER_PATTERN, YOUTUBE_PATTERNS

logger = logging.getLogger(__name__)


class MarkdownService:
    """Handles markdown rendering for guide content."""

    @staticmethod
    def render_section(section, use_cache=True):
        """
        Render a section's markdown to HTML.

        Args:
            section: GuideSection instance
            use_cache: Whether to use cached result

        Returns:
            str: Rendered HTML (marked safe)
        """
        cache_key = f"guide:section:render:{section.id}:{section.guide.updated_at.timestamp()}"

        if use_cache:
            cached = cache.get(cache_key)
            if cached:
                return mark_safe(cached)

        # Get game for trophy lookups
        game = section.guide.game

        # Process content
        content = section.content
        content = MarkdownService._process_trophy_mentions(content, game)
        content = MarkdownService._process_spoiler_tags(content)
        content = MarkdownService._process_youtube_embeds(content)

        # Render markdown
        md = markdown.Markdown(extensions=[
            'tables',
            'fenced_code',
            'nl2br',
            'extra',
        ])
        html = md.convert(content)

        # Cache and return
        if use_cache:
            cache.set(cache_key, html, RENDER_CACHE_TIMEOUT)

        return mark_safe(html)

    @staticmethod
    def render_guide(guide, use_cache=True):
        """
        Render all sections of a guide.

        Args:
            guide: Guide instance
            use_cache: Whether to use cached results

        Returns:
            list[dict]: [{'title': str, 'slug': str, 'html': str, 'order': int}]
        """
        sections = guide.sections.all().order_by('section_order')

        rendered = []
        for section in sections:
            rendered.append({
                'title': section.title,
                'slug': section.slug,
                'order': section.section_order,
                'html': MarkdownService.render_section(section, use_cache=use_cache),
            })

        return rendered

    @staticmethod
    def _process_trophy_mentions(content, game):
        """
        Replace [trophy:id] with trophy icon and name.

        Args:
            content: Markdown content string
            game: Game instance for trophy lookup

        Returns:
            str: Content with trophy mentions replaced
        """
        # Pre-fetch all trophies for this game
        trophies = {t.trophy_id: t for t in game.trophies.all()}

        def replace_trophy(match):
            trophy_id = int(match.group(1))
            trophy = trophies.get(trophy_id)

            if not trophy:
                return match.group(0)

            icon_html = ''
            if trophy.trophy_icon_url:
                icon_html = (
                    f'<img src="{escape(trophy.trophy_icon_url)}" alt="" '
                    f'class="trophy-mention-icon" loading="lazy">'
                )

            type_class = f"trophy-{trophy.trophy_type.lower()}"
            name_escaped = escape(trophy.trophy_name)

            return (
                f'<span class="trophy-mention {type_class}">'
                f'{icon_html}'
                f'<span class="trophy-mention-name">{name_escaped}</span>'
                f'</span>'
            )

        return TROPHY_PATTERN.sub(replace_trophy, content)

    @staticmethod
    def _process_spoiler_tags(content):
        """
        Replace ||spoiler text|| with clickable spoiler elements.

        Args:
            content: Markdown content string

        Returns:
            str: Content with spoiler tags replaced
        """
        def replace_spoiler(match):
            spoiler_text = escape(match.group(1))
            return (
                f'<span class="spoiler" tabindex="0" role="button" '
                f'aria-label="Click to reveal spoiler">'
                f'<span class="spoiler-content">{spoiler_text}</span>'
                f'</span>'
            )

        return SPOILER_PATTERN.sub(replace_spoiler, content)

    @staticmethod
    def _process_youtube_embeds(content):
        """
        Replace YouTube URLs with responsive iframe embeds.

        Args:
            content: Markdown content string

        Returns:
            str: Content with YouTube URLs replaced with embeds
        """
        def replace_youtube(match):
            video_id = match.group(1)
            return (
                f'<div class="video-embed">'
                f'<iframe src="https://www.youtube-nocookie.com/embed/{video_id}" '
                f'frameborder="0" allowfullscreen loading="lazy" '
                f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
                f'gyroscope; picture-in-picture">'
                f'</iframe>'
                f'</div>'
            )

        result = content
        for pattern in YOUTUBE_PATTERNS:
            result = pattern.sub(replace_youtube, result)

        return result

    @staticmethod
    def get_trophy_list_for_editor(game):
        """
        Get trophy list for editor autocomplete.

        Args:
            game: Game instance

        Returns:
            list[dict]: [{'id': int, 'name': str, 'type': str, 'icon': str, 'mention': str}]
        """
        trophies = game.trophies.all().order_by('trophy_id')

        return [
            {
                'id': t.trophy_id,
                'name': t.trophy_name,
                'type': t.trophy_type,
                'icon': t.trophy_icon_url or '',
                'mention': f'[trophy:{t.trophy_id}]',
            }
            for t in trophies
        ]

    @staticmethod
    def preview_content(content, game):
        """
        Preview markdown content without caching.

        Args:
            content: Raw markdown content
            game: Game instance for trophy lookups

        Returns:
            str: Rendered HTML (marked safe)
        """
        processed = content
        processed = MarkdownService._process_trophy_mentions(processed, game)
        processed = MarkdownService._process_spoiler_tags(processed)
        processed = MarkdownService._process_youtube_embeds(processed)

        md = markdown.Markdown(extensions=[
            'tables',
            'fenced_code',
            'nl2br',
            'extra',
        ])
        html = md.convert(processed)

        return mark_safe(html)

    @staticmethod
    def invalidate_section_cache(section):
        """
        Invalidate cached render for a section.

        Args:
            section: GuideSection instance
        """
        cache_key = f"guide:section:render:{section.id}:{section.guide.updated_at.timestamp()}"
        cache.delete(cache_key)
        logger.debug(f"Section cache invalidated: {section.id}")

    @staticmethod
    def invalidate_guide_cache(guide):
        """
        Invalidate cached renders for all sections.

        Updates guide.updated_at to invalidate timestamp-based cache keys.
        """
        from django.utils import timezone
        guide.updated_at = timezone.now()
        guide.save(update_fields=['updated_at'])
        logger.debug(f"Guide cache invalidated: {guide.id}")
