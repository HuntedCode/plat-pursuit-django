"""
ShareImageService - Generates platinum share images using Pillow.
Follows the service layer pattern used in notification_service.py.
Card-based design matching PlatPursuit game cards.
"""
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.conf import settings
from pathlib import Path
import requests
import logging
import sys

logger = logging.getLogger(__name__)


class ShareImageService:
    """Service for generating platinum share images."""

    # Image dimensions
    DIMENSIONS = {
        'landscape': (1200, 630),   # Facebook/Twitter/Discord optimal
        'portrait': (1080, 1350),   # Instagram post
    }

    # PlatPursuit color scheme (matching DaisyUI theme)
    COLORS = {
        'background': (26, 32, 44),        # Dark base
        'background_light': (45, 55, 72),  # Slightly lighter
        'card_bg': (25, 30, 36),           # Card background
        'card_border': (61, 68, 81),       # Card border
        'primary': (14, 165, 233),         # Cyan/sky blue
        'text_primary': (255, 255, 255),   # White
        'text_secondary': (156, 163, 175), # Gray-400
        'gold': (234, 179, 8),             # Trophy gold
        'platinum': (160, 210, 219),       # Platinum color
        'warning': (251, 189, 35),         # Warning/earn rate
    }

    # Font paths
    FONT_DIR = Path(settings.BASE_DIR) / 'static' / 'fonts'

    @staticmethod
    def _to_int(value, default=0):
        """Safely convert value to int."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _to_float(value, default=None):
        """Safely convert value to float."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def generate_image(cls, notification, format_type):
        """
        Generate a share image for a platinum notification.

        Args:
            notification: Notification instance with platinum metadata
            format_type: 'landscape' or 'portrait'

        Returns:
            InMemoryUploadedFile: Generated image file ready for saving
        """
        if format_type not in cls.DIMENSIONS:
            raise ValueError(f"Invalid format: {format_type}. Must be 'landscape' or 'portrait'")

        width, height = cls.DIMENSIONS[format_type]
        metadata = notification.metadata or {}

        # Create base image with gradient background
        img = cls._create_background(width, height)

        # Load fonts
        fonts = cls._load_fonts()

        # Render card-based layout
        if format_type == 'landscape':
            cls._render_landscape_card(img, fonts, metadata, width, height)
        else:
            cls._render_portrait_card(img, fonts, metadata, width, height)

        # Convert to Django file
        return cls._image_to_file(img, notification.id, format_type)

    @classmethod
    def _create_background(cls, width, height):
        """Create gradient background image."""
        img = Image.new('RGB', (width, height), cls.COLORS['background'])

        # Create diagonal gradient
        for y in range(height):
            for x in range(width):
                progress = (x / width * 0.5 + y / height * 0.5)
                r = int(cls.COLORS['background'][0] + (cls.COLORS['background_light'][0] - cls.COLORS['background'][0]) * progress * 0.5)
                g = int(cls.COLORS['background'][1] + (cls.COLORS['background_light'][1] - cls.COLORS['background'][1]) * progress * 0.5)
                b = int(cls.COLORS['background'][2] + (cls.COLORS['background_light'][2] - cls.COLORS['background'][2]) * progress * 0.5)
                img.putpixel((x, y), (r, g, b))

        return img

    @classmethod
    def _load_fonts(cls):
        """Load fonts with fallback to default system fonts."""
        fonts = {}

        # Define font size mappings
        font_sizes = {
            'title_large': 38,
            'title': 32,
            'subtitle': 28,
            'body': 24,
            'small': 20,
            'stat_large': 32,
            'stat': 20,
        }

        # Try to load custom fonts from static/fonts directory
        try:
            poppins_bold = cls.FONT_DIR / 'Poppins-Bold.ttf'
            poppins_semibold = cls.FONT_DIR / 'Poppins-SemiBold.ttf'
            inter_regular = cls.FONT_DIR / 'Inter_24pt-Regular.ttf'

            if poppins_bold.exists():
                fonts['title_large'] = ImageFont.truetype(str(poppins_bold), font_sizes['title_large'])
                fonts['title'] = ImageFont.truetype(str(poppins_bold), font_sizes['title'])
                fonts['stat_large'] = ImageFont.truetype(str(poppins_bold), font_sizes['stat_large'])
            if poppins_semibold.exists():
                fonts['subtitle'] = ImageFont.truetype(str(poppins_semibold), font_sizes['subtitle'])
            elif poppins_bold.exists():
                fonts['subtitle'] = ImageFont.truetype(str(poppins_bold), font_sizes['subtitle'])
            if inter_regular.exists():
                fonts['body'] = ImageFont.truetype(str(inter_regular), font_sizes['body'])
                fonts['small'] = ImageFont.truetype(str(inter_regular), font_sizes['small'])
                fonts['stat'] = ImageFont.truetype(str(inter_regular), font_sizes['stat'])
        except Exception as e:
            logger.warning(f"Failed to load custom fonts: {e}")

        # Fall back to system fonts
        cls._load_fallback_fonts(fonts, font_sizes)

        return fonts

    @classmethod
    def _load_fallback_fonts(cls, fonts, font_sizes):
        """Load fallback system fonts."""
        system_fonts = ['arial', 'Arial', 'DejaVuSans', 'FreeSans', 'Helvetica']

        for key in ['title_large', 'title', 'subtitle', 'stat_large']:
            if key not in fonts:
                for font_name in system_fonts:
                    try:
                        fonts[key] = ImageFont.truetype(font_name, font_sizes.get(key, 32))
                        break
                    except OSError:
                        continue

        for key in ['body', 'small', 'stat']:
            if key not in fonts:
                for font_name in system_fonts:
                    try:
                        fonts[key] = ImageFont.truetype(font_name, font_sizes.get(key, 20))
                        break
                    except OSError:
                        continue

        # Ultimate fallback
        default_font = ImageFont.load_default()
        for key in font_sizes.keys():
            if key not in fonts:
                fonts[key] = default_font

    @classmethod
    def _render_landscape_card(cls, img, fonts, metadata, width, height):
        """Render landscape card layout (1200x630)."""
        draw = ImageDraw.Draw(img)

        # Card dimensions
        card_margin = 30
        card_x = card_margin
        card_y = card_margin
        card_width = width - (card_margin * 2)
        card_height = height - (card_margin * 2)

        # Draw card background
        cls._draw_rounded_rect(img, card_x, card_y, card_width, card_height, 16, cls.COLORS['card_bg'])

        # Draw card border
        cls._draw_rounded_rect_border(draw, card_x, card_y, card_width, card_height, 16, cls.COLORS['platinum'], 4)

        # Extract metadata with fallbacks (ensure numeric types)
        game_name = metadata.get('game_name', 'Unknown Game')
        username = metadata.get('username', 'Player')
        total_plats = cls._to_int(metadata.get('user_total_platinums', 0))
        progress = cls._to_int(metadata.get('progress_percentage', 0))
        earned_trophies = cls._to_int(metadata.get('earned_trophies_count', 0))
        total_trophies = cls._to_int(metadata.get('total_trophies_count', 0))
        game_image_url = metadata.get('game_image', '')
        trophy_icon_url = metadata.get('trophy_icon_url', '')
        rarity_label = metadata.get('rarity_label', '')
        earn_rate = cls._to_float(metadata.get('trophy_earn_rate'))

        # Header section
        header_y = card_y + 24
        header_height = 80

        # Draw header border
        draw.line([(card_x, header_y + header_height), (card_x + card_width, header_y + header_height)],
                  fill=cls.COLORS['card_border'], width=2)

        # Game title (allow wrapping)
        title_x = card_x + 30
        title_max_width = card_width - 200  # Leave room for platinum badge
        wrapped_title = cls._wrap_text(game_name, fonts['title'], title_max_width)
        draw.text((title_x, header_y), wrapped_title, font=fonts['title'], fill=cls.COLORS['text_primary'])

        # Username below title
        draw.text((title_x, header_y + 40), f"Earned by ", font=fonts['body'], fill=cls.COLORS['text_secondary'])
        username_x = title_x + draw.textbbox((0, 0), "Earned by ", font=fonts['body'])[2]
        draw.text((username_x, header_y + 40), username, font=fonts['body'], fill=cls.COLORS['primary'])

        # Platinum badge (top right)
        if total_plats and total_plats > 0:
            badge_x = card_x + card_width - 140
            badge_y = header_y
            cls._draw_platinum_badge(draw, badge_x, badge_y, total_plats, fonts)

        # Main content section
        content_y = header_y + header_height + 24
        content_height = card_height - header_height - 100

        # Left: Game image with trophy overlay
        image_x = card_x + 30
        image_y = content_y
        image_size = min(340, content_height)

        # Draw image container border
        cls._draw_rounded_rect_border(draw, image_x - 2, image_y - 2, image_size + 4, image_size + 4, 12, cls.COLORS['card_border'], 4)

        # Fetch and paste game image
        if game_image_url:
            game_img = cls._fetch_and_process_image(game_image_url, (image_size, image_size))
            if game_img:
                img.paste(game_img, (image_x, image_y))

        # Trophy icon overlay (bottom right of game image)
        trophy_size = 90
        trophy_x = image_x + image_size - trophy_size + 10
        trophy_y = image_y + image_size - trophy_size + 10

        # Trophy container background
        cls._draw_rounded_rect(img, trophy_x, trophy_y, trophy_size, trophy_size, 12, cls.COLORS['card_bg'])
        cls._draw_rounded_rect_border(draw, trophy_x, trophy_y, trophy_size, trophy_size, 12, cls.COLORS['platinum'], 3)

        if trophy_icon_url:
            trophy_img = cls._fetch_and_process_image(trophy_icon_url, (trophy_size - 6, trophy_size - 6))
            if trophy_img:
                img.paste(trophy_img, (trophy_x + 3, trophy_y + 3))

        # Right: Stats and info
        info_x = image_x + image_size + 30
        info_y = content_y + 10

        # "PLATINUM EARNED" header
        draw.text((info_x, info_y), "PLATINUM EARNED", font=fonts['title_large'], fill=cls.COLORS['platinum'])
        info_y += 60

        # Stats row
        stats_parts = []
        if earned_trophies and total_trophies:
            stats_parts.append(f"{earned_trophies}/{total_trophies} Trophies")
        if earn_rate is not None and earn_rate > 0:
            stats_parts.append(f"{earn_rate:.2f}% Earn Rate")
        if metadata.get('play_duration_seconds'):
            hours = int(metadata['play_duration_seconds'] // 3600)
            if hours > 0:
                stats_parts.append(f"{hours}h Played")

        if stats_parts:
            stats_text = "  |  ".join(stats_parts)
            draw.text((info_x, info_y), stats_text, font=fonts['stat'], fill=cls.COLORS['text_secondary'])
            info_y += 40

        # Rarity badge
        if rarity_label:
            cls._draw_badge(draw, info_x, info_y, rarity_label, cls.COLORS['platinum'], fonts['small'])

        # Footer
        footer_y = card_y + card_height - 40
        draw.line([(card_x, footer_y - 12), (card_x + card_width, footer_y - 12)],
                  fill=cls.COLORS['card_border'], width=2)

        # Progress on left
        if progress:
            draw.text((card_x + 30, footer_y), f"{progress}% Complete", font=fonts['small'], fill=cls.COLORS['text_secondary'])

        # Branding on right
        branding = "platpursuit.com"
        branding_bbox = draw.textbbox((0, 0), branding, font=fonts['small'])
        draw.text((card_x + card_width - 30 - branding_bbox[2], footer_y), branding, font=fonts['small'], fill=cls.COLORS['text_secondary'])

    @classmethod
    def _render_portrait_card(cls, img, fonts, metadata, width, height):
        """Render portrait card layout (1080x1350)."""
        draw = ImageDraw.Draw(img)

        # Card dimensions
        card_margin = 30
        card_x = card_margin
        card_y = card_margin
        card_width = width - (card_margin * 2)
        card_height = height - (card_margin * 2)

        # Draw card background
        cls._draw_rounded_rect(img, card_x, card_y, card_width, card_height, 16, cls.COLORS['card_bg'])

        # Draw card border
        cls._draw_rounded_rect_border(draw, card_x, card_y, card_width, card_height, 16, cls.COLORS['platinum'], 4)

        # Extract metadata (ensure numeric types)
        game_name = metadata.get('game_name', 'Unknown Game')
        username = metadata.get('username', 'Player')
        total_plats = cls._to_int(metadata.get('user_total_platinums', 0))
        progress = cls._to_int(metadata.get('progress_percentage', 0))
        earned_trophies = cls._to_int(metadata.get('earned_trophies_count', 0))
        total_trophies = cls._to_int(metadata.get('total_trophies_count', 0))
        game_image_url = metadata.get('game_image', '')
        trophy_icon_url = metadata.get('trophy_icon_url', '')
        rarity_label = metadata.get('rarity_label', '')
        earn_rate = cls._to_float(metadata.get('trophy_earn_rate'))
        play_duration = cls._to_float(metadata.get('play_duration_seconds'))

        # Game image section (top)
        image_height = 480
        if game_image_url:
            game_img = cls._fetch_and_process_image(game_image_url, (card_width, image_height), cover=True)
            if game_img:
                # Create mask for rounded top corners
                img.paste(game_img, (card_x, card_y))

        # Gradient overlay on image
        cls._add_gradient_overlay(img, card_x, card_y + image_height - 180, card_width, card_y + image_height, cls.COLORS['card_bg'])

        # Trophy icon (bottom right of image)
        trophy_size = 110
        trophy_x = card_x + card_width - trophy_size - 20
        trophy_y = card_y + image_height - trophy_size - 20
        cls._draw_rounded_rect(img, trophy_x, trophy_y, trophy_size, trophy_size, 16, cls.COLORS['card_bg'])
        cls._draw_rounded_rect_border(draw, trophy_x, trophy_y, trophy_size, trophy_size, 16, cls.COLORS['platinum'], 4)

        if trophy_icon_url:
            trophy_img = cls._fetch_and_process_image(trophy_icon_url, (trophy_size - 8, trophy_size - 8))
            if trophy_img:
                img.paste(trophy_img, (trophy_x + 4, trophy_y + 4))

        # Content section
        content_y = card_y + image_height + 20
        center_x = card_x + card_width // 2

        # "PLATINUM EARNED" header
        cls._draw_centered_text(draw, "PLATINUM EARNED", center_x, content_y, fonts['title_large'], cls.COLORS['platinum'])
        content_y += 60

        # Game title (centered, allow wrapping)
        wrapped_title = cls._wrap_text(game_name, fonts['title'], card_width - 80)
        title_bbox = draw.textbbox((0, 0), wrapped_title, font=fonts['title'])
        title_height = title_bbox[3] - title_bbox[1]
        cls._draw_centered_text(draw, wrapped_title, center_x, content_y, fonts['title'], cls.COLORS['text_primary'])
        content_y += title_height + 20

        # Username
        earned_text = f"Earned by {username}"
        cls._draw_centered_text(draw, earned_text, center_x, content_y, fonts['body'], cls.COLORS['text_secondary'])
        content_y += 50

        # Stats grid (2x2)
        grid_x = card_x + 40
        grid_y = content_y
        grid_width = (card_width - 100) // 2
        grid_height = 80
        grid_gap = 20

        stats = []
        if earned_trophies and total_trophies:
            stats.append((f"{earned_trophies}/{total_trophies}", "Trophies", cls.COLORS['platinum']))
        if progress:
            stats.append((f"{progress}%", "Complete", cls.COLORS['primary']))
        if earn_rate is not None and earn_rate > 0:
            stats.append((f"{earn_rate:.1f}%", "Earn Rate", cls.COLORS['warning']))
        if play_duration:
            hours = int(play_duration // 3600)
            minutes = int((play_duration % 3600) // 60)
            stats.append((f"{hours}h {minutes}m", "Playtime", cls.COLORS['text_primary']))

        for i, (value, label, color) in enumerate(stats[:4]):
            col = i % 2
            row = i // 2
            stat_x = grid_x + col * (grid_width + grid_gap)
            stat_y = grid_y + row * (grid_height + grid_gap)

            # Stat background
            cls._draw_rounded_rect(img, stat_x, stat_y, grid_width, grid_height, 12,
                                   (cls.COLORS['card_border'][0] // 2, cls.COLORS['card_border'][1] // 2, cls.COLORS['card_border'][2] // 2))

            # Stat value
            stat_center_x = stat_x + grid_width // 2
            cls._draw_centered_text(draw, value, stat_center_x, stat_y + 15, fonts['stat_large'], color)
            cls._draw_centered_text(draw, label, stat_center_x, stat_y + 50, fonts['stat'], cls.COLORS['text_secondary'])

        content_y = grid_y + (2 * (grid_height + grid_gap)) + 20

        # Badges row (rarity + platinum number)
        badges_y = content_y + 10
        badge_x = center_x - 100
        if rarity_label:
            cls._draw_badge(draw, badge_x, badges_y, rarity_label, cls.COLORS['platinum'], fonts['small'])
            badge_x += 160
        if total_plats and total_plats > 0:
            cls._draw_badge(draw, badge_x, badges_y, f"Platinum #{total_plats}", cls.COLORS['gold'], fonts['small'])

        # Footer
        footer_y = card_y + card_height - 50
        draw.line([(card_x, footer_y - 16), (card_x + card_width, footer_y - 16)],
                  fill=cls.COLORS['card_border'], width=2)

        branding = "platpursuit.com"
        cls._draw_centered_text(draw, branding, center_x, footer_y, fonts['small'], cls.COLORS['text_secondary'])

    @classmethod
    def _draw_rounded_rect(cls, img, x, y, width, height, radius, color):
        """Draw a filled rounded rectangle."""
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([x, y, x + width, y + height], radius=radius, fill=color)

    @classmethod
    def _draw_rounded_rect_border(cls, draw, x, y, width, height, radius, color, border_width):
        """Draw a rounded rectangle border."""
        draw.rounded_rectangle([x, y, x + width, y + height], radius=radius, outline=color, width=border_width)

    @classmethod
    def _draw_platinum_badge(cls, draw, x, y, number, fonts):
        """Draw platinum number badge."""
        # Badge background (semi-transparent effect via solid color)
        badge_color = (cls.COLORS['platinum'][0] // 4, cls.COLORS['platinum'][1] // 4, cls.COLORS['platinum'][2] // 4)
        draw.rounded_rectangle([x, y, x + 120, y + 70], radius=12, fill=badge_color, outline=cls.COLORS['platinum'], width=2)

        # "Platinum" label
        draw.text((x + 20, y + 10), "Platinum", font=fonts['stat'], fill=cls.COLORS['text_secondary'])
        # Number
        draw.text((x + 35, y + 32), f"#{number}", font=fonts['stat_large'], fill=cls.COLORS['platinum'])

    @classmethod
    def _draw_badge(cls, draw, x, y, text, color, font):
        """Draw a pill-shaped badge."""
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        padding_x = 16
        padding_y = 8
        badge_width = text_width + padding_x * 2
        badge_height = 36

        # Background
        bg_color = (color[0] // 6, color[1] // 6, color[2] // 6)
        draw.rounded_rectangle([x, y, x + badge_width, y + badge_height], radius=18, fill=bg_color, outline=color, width=2)

        # Text
        draw.text((x + padding_x, y + padding_y), text, font=font, fill=color)

    @classmethod
    def _wrap_text(cls, text, font, max_width):
        """Wrap text to fit within max_width."""
        # Simple implementation - just return first line if too long
        # For proper wrapping, would need to measure each word
        if len(text) > 40:
            return text[:37] + "..."
        return text

    @classmethod
    def _fetch_and_process_image(cls, url, size, cover=False):
        """Fetch image from URL and resize/crop as needed."""
        if not url:
            return None

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            fetched_img = Image.open(BytesIO(response.content))
            fetched_img = fetched_img.convert('RGB')

            if cover:
                # Cover mode: resize to fill, then crop
                img_ratio = fetched_img.width / fetched_img.height
                target_ratio = size[0] / size[1]

                if img_ratio > target_ratio:
                    new_height = size[1]
                    new_width = int(new_height * img_ratio)
                else:
                    new_width = size[0]
                    new_height = int(new_width / img_ratio)

                fetched_img = fetched_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                left = (new_width - size[0]) // 2
                top = (new_height - size[1]) // 2
                fetched_img = fetched_img.crop((left, top, left + size[0], top + size[1]))
            else:
                fetched_img.thumbnail(size, Image.Resampling.LANCZOS)

                result = Image.new('RGB', size, cls.COLORS['card_bg'])
                x = (size[0] - fetched_img.width) // 2
                y = (size[1] - fetched_img.height) // 2
                result.paste(fetched_img, (x, y))
                fetched_img = result

            return fetched_img

        except Exception as e:
            logger.warning(f"Failed to fetch/process image from {url}: {e}")
            return None

    @classmethod
    def _add_gradient_overlay(cls, img, x, y_start, width, y_end, target_color):
        """Add a gradient overlay for text readability over images."""
        overlay = Image.new('RGBA', (width, y_end - y_start), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        for y in range(y_end - y_start):
            alpha = int(255 * (y / (y_end - y_start)))
            overlay_draw.line([(0, y), (width, y)], fill=(target_color[0], target_color[1], target_color[2], alpha))

        img_rgba = img.convert('RGBA')
        img_rgba.paste(overlay, (x, y_start), overlay)

        rgb_result = img_rgba.convert('RGB')
        img.paste(rgb_result)

    @classmethod
    def _draw_centered_text(cls, draw, text, center_x, y, font, fill):
        """Draw text centered at x position."""
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        x = center_x - text_width // 2
        draw.text((x, y), text, font=font, fill=fill)

    @classmethod
    def _image_to_file(cls, img, notification_id, format_type):
        """Convert PIL Image to Django InMemoryUploadedFile."""
        output = BytesIO()
        img.save(output, format='PNG', optimize=True)
        output.seek(0)

        filename = f"platinum_share_{notification_id}_{format_type}.png"

        return InMemoryUploadedFile(
            file=output,
            field_name='image',
            name=filename,
            content_type='image/png',
            size=sys.getsizeof(output),
            charset=None
        )
