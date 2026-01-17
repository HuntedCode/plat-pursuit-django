"""
Comprehensive test suite for MarkdownService.

Tests all functionality outlined in the implementation document including:
- Section and guide rendering
- Trophy mention processing
- Spoiler tag processing
- YouTube embed processing
- Caching behavior
- Editor support methods
- Cache invalidation
"""
from django.test import TestCase
from django.core.cache import cache
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock

from trophies.models import Profile, Game, Concept, Guide, GuideSection, Trophy
from trophies.services.markdown_service import MarkdownService

User = get_user_model()


class MarkdownServiceTestCase(TestCase):
    """Base test case with common fixtures for markdown service tests."""

    def setUp(self):
        """Create common test fixtures."""
        # Clear cache before each test
        cache.clear()

        # Create user and profile
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            psn_username='TestUser',
            user=self.user,
            account_id='12345'
        )

        # Create game concept and game
        self.concept = Concept.objects.create(
            unified_title='Test Game',
        )
        self.game = Game.objects.create(
            np_communication_id='NPWR12345_00',
            concept=self.concept,
            title_name='Test Game',
            title_platform=['PS5']
        )

        # Create trophies for testing mentions
        self.trophy_plat = Trophy.objects.create(
            game=self.game,
            trophy_id=1,
            trophy_type='Platinum',
            trophy_name='Master Trophy',
            trophy_detail='Complete all trophies',
            trophy_icon_url='https://example.com/plat.png'
        )
        self.trophy_gold = Trophy.objects.create(
            game=self.game,
            trophy_id=2,
            trophy_type='Gold',
            trophy_name='Expert Trophy',
            trophy_detail='Complete expert challenge',
            trophy_icon_url='https://example.com/gold.png'
        )
        self.trophy_no_icon = Trophy.objects.create(
            game=self.game,
            trophy_id=3,
            trophy_type='Bronze',
            trophy_name='Basic Trophy',
            trophy_detail='Basic achievement',
            trophy_icon_url=''
        )

        # Create guide
        self.guide = Guide.objects.create(
            game=self.game,
            author=self.profile,
            title='Test Guide',
            summary='Test guide description',
            status='published'
        )

        # Create guide section
        self.section = GuideSection.objects.create(
            guide=self.guide,
            title='Introduction',
            slug='introduction',
            content='# Test Content',
            section_order=1
        )


class TrophyMentionTests(MarkdownServiceTestCase):
    """Tests for trophy mention processing."""

    def test_valid_trophy_mention_with_icon(self):
        """Trophy mention should be replaced with HTML including icon."""
        content = 'Earn [trophy:1] to unlock the platinum.'
        result = MarkdownService._process_trophy_mentions(content, self.game)

        self.assertIn('trophy-mention', result)
        self.assertIn('trophy-platinum', result)
        self.assertIn('Master Trophy', result)
        self.assertIn('https://example.com/plat.png', result)
        self.assertIn('trophy-mention-icon', result)

    def test_valid_trophy_mention_without_icon(self):
        """Trophy mention without icon should still render."""
        content = 'Earn [trophy:3] first.'
        result = MarkdownService._process_trophy_mentions(content, self.game)

        self.assertIn('trophy-mention', result)
        self.assertIn('trophy-bronze', result)
        self.assertIn('Basic Trophy', result)
        self.assertNotIn('trophy-mention-icon', result)

    def test_invalid_trophy_mention(self):
        """Invalid trophy ID should pass through unchanged."""
        content = 'Invalid [trophy:999] mention.'
        result = MarkdownService._process_trophy_mentions(content, self.game)

        self.assertEqual(content, result)
        self.assertNotIn('trophy-mention', result)

    def test_multiple_trophy_mentions(self):
        """Multiple trophy mentions should all be processed."""
        content = 'Earn [trophy:1] and [trophy:2] together.'
        result = MarkdownService._process_trophy_mentions(content, self.game)

        self.assertIn('Master Trophy', result)
        self.assertIn('Expert Trophy', result)
        # Each trophy mention creates 2 spans (outer trophy-mention + inner trophy-mention-name)
        # So 2 trophies = 4 total spans
        self.assertEqual(result.count('<span class="trophy-mention'), 4)

    def test_trophy_type_classes(self):
        """Trophy types should generate correct CSS classes."""
        # Test platinum
        content = '[trophy:1]'
        result = MarkdownService._process_trophy_mentions(content, self.game)
        self.assertIn('trophy-platinum', result)

        # Test gold
        content = '[trophy:2]'
        result = MarkdownService._process_trophy_mentions(content, self.game)
        self.assertIn('trophy-gold', result)

    def test_trophy_name_escaping(self):
        """Trophy names with HTML should be escaped."""
        trophy_xss = Trophy.objects.create(
            game=self.game,
            trophy_id=99,
            trophy_type='Bronze',
            trophy_name='<script>alert("xss")</script>',
            trophy_detail='XSS test'
        )

        content = '[trophy:99]'
        result = MarkdownService._process_trophy_mentions(content, self.game)

        self.assertNotIn('<script>', result)
        self.assertIn('&lt;script&gt;', result)


class SpoilerTagTests(MarkdownServiceTestCase):
    """Tests for spoiler tag processing."""

    def test_basic_spoiler(self):
        """Basic spoiler should be wrapped in spoiler HTML."""
        content = 'The answer is ||hidden text||.'
        result = MarkdownService._process_spoiler_tags(content)

        self.assertIn('class="spoiler"', result)
        self.assertIn('class="spoiler-content"', result)
        self.assertIn('hidden text', result)
        self.assertIn('tabindex="0"', result)
        self.assertIn('role="button"', result)

    def test_multiline_spoiler(self):
        """Spoilers can span multiple lines."""
        content = '||Line 1\nLine 2\nLine 3||'
        result = MarkdownService._process_spoiler_tags(content)

        self.assertIn('spoiler', result)
        self.assertIn('Line 1', result)
        self.assertIn('Line 3', result)

    def test_multiple_spoilers(self):
        """Multiple spoilers should all be processed."""
        content = '||Spoiler 1|| and ||Spoiler 2||.'
        result = MarkdownService._process_spoiler_tags(content)

        self.assertEqual(result.count('class="spoiler"'), 2)
        self.assertIn('Spoiler 1', result)
        self.assertIn('Spoiler 2', result)

    def test_spoiler_escaping(self):
        """Spoiler content should be HTML-escaped."""
        content = '||<script>alert("xss")</script>||'
        result = MarkdownService._process_spoiler_tags(content)

        self.assertNotIn('<script>', result)
        self.assertIn('&lt;script&gt;', result)

    def test_empty_spoiler(self):
        """Empty spoilers should still render."""
        content = '||||'
        result = MarkdownService._process_spoiler_tags(content)

        self.assertIn('spoiler', result)


class YouTubeEmbedTests(MarkdownServiceTestCase):
    """Tests for YouTube embed processing."""

    def test_youtube_watch_url(self):
        """Standard youtube.com/watch URLs should be converted."""
        content = 'Check out https://www.youtube.com/watch?v=abc123defgh'
        result = MarkdownService._process_youtube_embeds(content)

        self.assertIn('video-embed', result)
        self.assertIn('youtube-nocookie.com/embed/abc123defgh', result)
        self.assertIn('iframe', result)

    def test_youtube_short_url(self):
        """Short youtu.be URLs should be converted."""
        content = 'Watch https://youtu.be/xyz987abcde'
        result = MarkdownService._process_youtube_embeds(content)

        self.assertIn('video-embed', result)
        self.assertIn('youtube-nocookie.com/embed/xyz987abcde', result)

    def test_multiple_youtube_urls(self):
        """Multiple YouTube URLs should all be converted."""
        content = (
            'First: https://www.youtube.com/watch?v=abc123defgh\n'
            'Second: https://youtu.be/xyz987abcde'
        )
        result = MarkdownService._process_youtube_embeds(content)

        self.assertEqual(result.count('video-embed'), 2)
        self.assertIn('abc123defgh', result)
        self.assertIn('xyz987abcde', result)

    def test_invalid_youtube_url(self):
        """Invalid YouTube URLs should not be converted."""
        # Too short video ID
        content = 'Invalid https://www.youtube.com/watch?v=short'
        result = MarkdownService._process_youtube_embeds(content)

        self.assertNotIn('video-embed', result)
        self.assertEqual(content, result)

    def test_youtube_privacy_enhanced(self):
        """Should use youtube-nocookie.com for privacy."""
        content = 'https://www.youtube.com/watch?v=abc123defgh'
        result = MarkdownService._process_youtube_embeds(content)

        self.assertIn('youtube-nocookie.com', result)
        self.assertNotIn('youtube.com/embed', result)

    def test_youtube_iframe_attributes(self):
        """Iframe should have correct attributes."""
        content = 'https://youtu.be/abc123defgh'
        result = MarkdownService._process_youtube_embeds(content)

        self.assertIn('frameborder="0"', result)
        self.assertIn('allowfullscreen', result)
        self.assertIn('loading="lazy"', result)
        self.assertIn('allow=', result)


class RenderSectionTests(MarkdownServiceTestCase):
    """Tests for render_section method."""

    def test_basic_markdown_rendering(self):
        """Basic markdown should be converted to HTML."""
        self.section.content = '# Header\n\n**Bold** and *italic*'
        self.section.save()

        result = MarkdownService.render_section(self.section, use_cache=False)

        self.assertIn('<h1>Header</h1>', result)
        self.assertIn('<strong>Bold</strong>', result)
        self.assertIn('<em>italic</em>', result)

    def test_table_rendering(self):
        """GFM tables should be rendered."""
        self.section.content = (
            '| Header 1 | Header 2 |\n'
            '|----------|----------|\n'
            '| Cell 1   | Cell 2   |'
        )
        self.section.save()

        result = MarkdownService.render_section(self.section, use_cache=False)

        self.assertIn('<table>', result)
        self.assertIn('<thead>', result)
        self.assertIn('<tbody>', result)
        self.assertIn('Header 1', result)
        self.assertIn('Cell 1', result)

    def test_fenced_code_blocks(self):
        """Fenced code blocks should be rendered."""
        self.section.content = '```python\nprint("hello")\n```'
        self.section.save()

        result = MarkdownService.render_section(self.section, use_cache=False)

        self.assertIn('<pre><code', result)
        self.assertIn('print(&quot;hello&quot;)', result)

    def test_all_custom_features_together(self):
        """All custom features should work together."""
        self.section.content = (
            '# Trophy Guide\n\n'
            'Earn [trophy:1] first.\n\n'
            'The solution is ||check behind the door||.\n\n'
            'Watch this: https://youtu.be/abc123defgh'
        )
        self.section.save()

        result = MarkdownService.render_section(self.section, use_cache=False)

        # Check all features processed
        self.assertIn('trophy-mention', result)
        self.assertIn('spoiler', result)
        self.assertIn('video-embed', result)
        self.assertIn('<h1>Trophy Guide</h1>', result)

    def test_caching_behavior(self):
        """Rendered content should be cached when use_cache=True."""
        self.section.content = '# Test'
        self.section.save()

        # First call - not cached
        with patch('trophies.services.markdown_service.cache') as mock_cache:
            mock_cache.get.return_value = None
            mock_cache.set.return_value = None

            MarkdownService.render_section(self.section, use_cache=True)

            # Should check cache and set it
            mock_cache.get.assert_called_once()
            mock_cache.set.assert_called_once()

    def test_cache_hit(self):
        """Cached content should be returned without re-rendering."""
        self.section.content = '# Cached'
        self.section.save()

        # Set up cache with pre-rendered content
        cache_key = f"guide:section:render:{self.section.id}:{self.section.guide.updated_at.timestamp()}"
        cached_html = '<h1>From Cache</h1>'
        cache.set(cache_key, cached_html)

        result = MarkdownService.render_section(self.section, use_cache=True)

        self.assertEqual(result, cached_html)

    def test_no_caching_when_disabled(self):
        """No caching should occur when use_cache=False."""
        self.section.content = '# No Cache'
        self.section.save()

        with patch('trophies.services.markdown_service.cache') as mock_cache:
            MarkdownService.render_section(self.section, use_cache=False)

            # Should not interact with cache
            mock_cache.get.assert_not_called()
            mock_cache.set.assert_not_called()


class RenderGuideTests(MarkdownServiceTestCase):
    """Tests for render_guide method."""

    def test_render_single_section_guide(self):
        """Guide with one section should render correctly."""
        self.section.content = '# Section 1'
        self.section.save()

        result = MarkdownService.render_guide(self.guide, use_cache=False)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['title'], 'Introduction')
        self.assertEqual(result[0]['slug'], 'introduction')
        self.assertEqual(result[0]['order'], 1)
        self.assertIn('<h1>Section 1</h1>', result[0]['html'])

    def test_render_multiple_sections(self):
        """Guide with multiple sections should render in order."""
        section2 = GuideSection.objects.create(
            guide=self.guide,
            title='Part 2',
            slug='part-2',
            content='# Second Section',
            section_order=2
        )
        section3 = GuideSection.objects.create(
            guide=self.guide,
            title='Part 3',
            slug='part-3',
            content='# Third Section',
            section_order=3
        )

        result = MarkdownService.render_guide(self.guide, use_cache=False)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]['title'], 'Introduction')
        self.assertEqual(result[1]['title'], 'Part 2')
        self.assertEqual(result[2]['title'], 'Part 3')
        self.assertEqual(result[0]['order'], 1)
        self.assertEqual(result[1]['order'], 2)
        self.assertEqual(result[2]['order'], 3)

    def test_section_order_respected(self):
        """Sections should be rendered in section_order, not creation order."""
        # Create sections in reverse order
        section_high = GuideSection.objects.create(
            guide=self.guide,
            title='Last',
            slug='last',
            content='Last',
            section_order=10
        )
        section_low = GuideSection.objects.create(
            guide=self.guide,
            title='First',
            slug='first',
            content='First',
            section_order=0
        )

        result = MarkdownService.render_guide(self.guide, use_cache=False)

        # Should be ordered by section_order
        self.assertEqual(result[0]['title'], 'First')
        self.assertEqual(result[-1]['title'], 'Last')


class EditorSupportTests(MarkdownServiceTestCase):
    """Tests for editor support methods."""

    def test_get_trophy_list_for_editor(self):
        """Should return trophy list with all required fields."""
        result = MarkdownService.get_trophy_list_for_editor(self.game)

        self.assertEqual(len(result), 3)

        # Check first trophy
        trophy_data = result[0]
        self.assertEqual(trophy_data['id'], 1)
        self.assertEqual(trophy_data['name'], 'Master Trophy')
        self.assertEqual(trophy_data['type'], 'Platinum')
        self.assertEqual(trophy_data['icon'], 'https://example.com/plat.png')
        self.assertEqual(trophy_data['mention'], '[trophy:1]')

    def test_trophy_list_ordered_by_id(self):
        """Trophy list should be ordered by trophy_id."""
        result = MarkdownService.get_trophy_list_for_editor(self.game)

        ids = [t['id'] for t in result]
        self.assertEqual(ids, sorted(ids))

    def test_trophy_list_empty_icon(self):
        """Trophies without icons should have empty string."""
        result = MarkdownService.get_trophy_list_for_editor(self.game)

        trophy_no_icon = [t for t in result if t['id'] == 3][0]
        self.assertEqual(trophy_no_icon['icon'], '')

    def test_preview_content(self):
        """Preview should render without caching."""
        content = (
            '# Preview\n\n'
            '[trophy:1] test\n\n'
            '||spoiler||\n\n'
            'https://youtu.be/abc123defgh'
        )

        with patch('trophies.services.markdown_service.cache') as mock_cache:
            result = MarkdownService.preview_content(content, self.game)

            # Should not interact with cache
            mock_cache.get.assert_not_called()
            mock_cache.set.assert_not_called()

        # Check all features processed
        self.assertIn('<h1>Preview</h1>', result)
        self.assertIn('trophy-mention', result)
        self.assertIn('spoiler', result)
        self.assertIn('video-embed', result)


class CacheInvalidationTests(MarkdownServiceTestCase):
    """Tests for cache invalidation methods."""

    def test_invalidate_section_cache(self):
        """Should log section cache invalidation."""
        with self.assertLogs('trophies.services.markdown_service', level='DEBUG') as logs:
            MarkdownService.invalidate_section_cache(self.section)

            self.assertTrue(any(f'Section cache invalidated: {self.section.id}' in log for log in logs.output))

    def test_invalidate_guide_cache_updates_timestamp(self):
        """Should update guide's updated_at timestamp."""
        original_updated_at = self.guide.updated_at

        # Wait a tiny bit to ensure timestamp changes
        import time
        time.sleep(0.01)

        MarkdownService.invalidate_guide_cache(self.guide)

        self.guide.refresh_from_db()
        self.assertGreater(self.guide.updated_at, original_updated_at)

    def test_invalidate_guide_cache_logs(self):
        """Should log guide cache invalidation."""
        with self.assertLogs('trophies.services.markdown_service', level='DEBUG') as logs:
            MarkdownService.invalidate_guide_cache(self.guide)

            self.assertTrue(any(f'Guide cache invalidated: {self.guide.id}' in log for log in logs.output))

    def test_cache_key_includes_timestamp(self):
        """Cache key should include updated_at timestamp for auto-invalidation."""
        # Render with initial timestamp
        result1 = MarkdownService.render_section(self.section, use_cache=True)

        # Update guide timestamp
        import time
        time.sleep(0.01)
        MarkdownService.invalidate_guide_cache(self.guide)

        # Refresh section to get updated guide timestamp
        self.section.refresh_from_db()

        # Render again - should be different cache key due to timestamp
        self.section.content = '# Updated'
        self.section.save()
        result2 = MarkdownService.render_section(self.section, use_cache=True)

        # Results should be different since cache key changed
        self.assertNotEqual(result1, result2)


class SecurityTests(MarkdownServiceTestCase):
    """Security tests to ensure XSS protection."""

    def test_xss_in_markdown_content(self):
        """
        XSS in markdown content.

        Note: Python markdown allows raw HTML by default. For user-generated content,
        you should implement additional HTML sanitization (e.g., using bleach library)
        at the view/input level. The custom syntax we process (trophy mentions,
        spoilers) is properly escaped before HTML generation.

        This test documents the current behavior - raw HTML passes through.
        """
        self.section.content = '<script>alert("xss")</script>'
        self.section.save()

        result = MarkdownService.render_section(self.section, use_cache=False)

        # Currently allows HTML passthrough - consider adding bleach sanitization
        # in production for user-generated content
        self.assertIn('<script>', result)

    def test_xss_in_trophy_names(self):
        """XSS in trophy names should be escaped."""
        # Already tested in TrophyMentionTests.test_trophy_name_escaping
        # but worth mentioning in security context
        pass

    def test_xss_in_spoilers(self):
        """XSS in spoiler content should be escaped."""
        # Already tested in SpoilerTagTests.test_spoiler_escaping
        # but worth mentioning in security context
        pass

    def test_youtube_id_validation(self):
        """Only valid YouTube IDs should be embedded."""
        # Test with SQL injection attempt in video ID
        content = 'https://www.youtube.com/watch?v=abc\'; DROP TABLE--'
        result = MarkdownService._process_youtube_embeds(content)

        # Should not match pattern, pass through unchanged
        self.assertEqual(content, result)
        self.assertNotIn('iframe', result)
