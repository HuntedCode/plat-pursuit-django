import json
import re

from django import template
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from datetime import datetime, timedelta
from humanize import naturaltime
from plat_pursuit.middleware import get_current_request
from trophies.util_modules.constants import BRONZE_STAGE_XP, SILVER_STAGE_XP, GOLD_STAGE_XP, PLAT_STAGE_XP

register = template.Library()

@register.filter
def iso_naturaltime(value):
    """Parse ISO string to datetime and apply naturaltime."""
    if not isinstance(value, str):
        return value
    try:
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'
        dt = datetime.fromisoformat(value)
        if not timezone.is_aware(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        return naturaltime(dt)
    except (ValueError, TypeError):
        return value

@register.filter
def iso_datetime(value):
    """Parse ISO string to datetime and apply naturaltime."""
    if not isinstance(value, str):
        return value
    try:
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'
        dt = datetime.fromisoformat(value)
        if not timezone.is_aware(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        return dt
    except (ValueError, TypeError):
        return value

@register.filter
def timedelta_hours(duration: timedelta) -> str:
    """Convert timedelta to total hours."""
    if not duration:
        return ''
    return f"{round(duration.total_seconds() / 3600, 2)} Hours"

@register.filter
def platform_color(game):
    platforms = game.title_platform
    if "PS5" in platforms:
        return 'primary'
    elif "PS4" in platforms:
        return 'accent'
    elif "PS3" in platforms:
        return 'warning'
    else:
        return 'secondary'

@register.filter
def platform_color_str(platform_str):
    if platform_str == "PS5":
        return 'primary'
    elif platform_str == "PS4":
        return 'accent'
    elif platform_str == "PS3":
        return 'warning'
    else:
        return 'secondary'

@register.filter
def platform_color_hex(platform_str):
    """
    Returns hex color for platform badge in share images.
    Matches DaisyUI theme colors from share_image_card.html template.
    """
    if platform_str == "PS5":
        return '#67d1f8'  # primary
    elif platform_str == "PS4":
        return '#ffaa5c'  # accent
    elif platform_str == "PS3":
        return '#fbbf24'  # warning (yellow-gold)
    else:
        return '#9580ff'  # secondary (PS Vita, etc.)

@register.filter
def region_color_hex(region_str):
    """
    Returns hex color for region badge in share images.
    Matches DaisyUI theme colors:
    - NA: primary (blue)
    - EU: secondary (purple)
    - JP: accent (orange)
    - AS: warning (yellow)
    """
    if region_str == "NA":
        return '#67d1f8'  # primary
    elif region_str == "EU":
        return '#9580ff'  # secondary
    elif region_str == "JP":
        return '#ffaa5c'  # accent
    elif region_str == "AS":
        return '#fbbf24'  # warning
    else:
        return '#9ca3af'  # neutral gray for unknown regions

@register.filter
def trophy_color(trophy):
    """Returns CSS class for trophy type. Accepts model instances or dicts."""
    trophy_type = trophy['trophy_type'] if isinstance(trophy, dict) else trophy.trophy_type
    colors = {'bronze': 'trophy-bronze', 'silver': 'trophy-silver', 'gold': 'trophy-gold', 'platinum': 'trophy-platinum'}
    return colors.get(trophy_type, '')

@register.filter
def trophy_css_color(trophy):
    """Returns full CSS variable, e.g. 'var(--color-trophy-gold)'. Accepts model instances or dicts."""
    return f'var(--color-{trophy_color(trophy)})'

@register.filter
def badge_color(tier):
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        return ''
    colors = {1: 'warning', 2: 'secondary', 3: 'error', 4: 'primary'}
    return colors.get(tier, '')

@register.filter
def badge_tier(tier):
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        return ''
    tiers = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
    return tiers.get(tier, '')

@register.filter
def badge_tier_xp(tier):
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        return 0
    xp = {1: BRONZE_STAGE_XP, 2: SILVER_STAGE_XP, 3: GOLD_STAGE_XP, 4: PLAT_STAGE_XP}
    return xp.get(tier, 0)

@register.filter
def multiply(value, arg):
    try:
        return float(value) * float(arg)
    except:
        return ''

@register.filter
def psn_rarity(rarity_int):
    labels = {0: 'Ultra Rare', 1: 'Very Rare', 2: 'Rare', 3: 'Common'}
    return labels.get(rarity_int, '')


@register.filter
def rarity_color_hex(earn_rate):
    """
    Returns a hex color based on trophy earn rate percentage.
    Used for share image rarity badges with inline styles.

    Rarity thresholds (based on PSN):
    - Ultra Rare: < 5%
    - Very Rare: 5-10%
    - Rare: 10-20%
    - Uncommon: 20-50%
    - Common: > 50%

    Returns dict with 'color' (hex) and 'bg' (rgba background).
    """
    if earn_rate is None:
        return {'color': '#67d1f8', 'bg': 'rgba(103, 209, 248, 0.15)'}

    try:
        rate = float(earn_rate)
    except (ValueError, TypeError):
        return {'color': '#67d1f8', 'bg': 'rgba(103, 209, 248, 0.15)'}

    if rate < 5:
        # Ultra Rare - Pink/Magenta (stands out, prestigious)
        return {'color': '#f472b6', 'bg': 'rgba(244, 114, 182, 0.15)'}
    elif rate < 10:
        # Very Rare - Lavender/Purple
        return {'color': '#a78bfa', 'bg': 'rgba(167, 139, 250, 0.15)'}
    elif rate < 20:
        # Rare - Yellow-Gold
        return {'color': '#fbbf24', 'bg': 'rgba(251, 191, 36, 0.15)'}
    elif rate < 50:
        # Uncommon - Emerald/Green
        return {'color': '#34d399', 'bg': 'rgba(52, 211, 153, 0.15)'}
    else:
        # Common - Slate/Gray
        return {'color': '#9ca3af', 'bg': 'rgba(156, 163, 175, 0.15)'}

@register.filter
def dict_get(dict_obj, key):
    return dict_obj.get(key)

@register.filter
def format_date(value, arg=None):
    """
    Format a datetime value according to user preferences.

    Automatically detects authenticated user's 24-hour clock preference.

    Args:
        value: datetime object to format
        arg: optional format specifier ('short', 'format_short', 'with_tz')

    Returns:
        Formatted date string
    """
    if value is None:
        return ''

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            try:
                value = datetime.strptime(value, '%b. %d, %Y, %I:%M %p')
            except ValueError:
                try:
                    value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    return value

    if not isinstance(value, (datetime, timezone.datetime)):
        return value

    current_tz = timezone.get_current_timezone()
    localized_value = value.astimezone(current_tz)

    # Try to get the current authenticated user's 24hr preference
    use_24hr = False
    try:
        request = get_current_request()
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            use_24hr = getattr(request.user, 'use_24hr_clock', False)
    except AttributeError:
        pass

    # Determine format string based on arg and user preference
    if arg == 'short':
        format_string = '%Y-%m-%d'
    elif arg == 'format_short':
        format_string = '%b. %d, %Y'
    else:
        # Default format - respect 24-hour preference
        if use_24hr:
            format_string = '%b. %d, %Y, %H:%M'  # 24-hour format
        else:
            format_string = '%b. %d, %Y, %I:%M %p'  # 12-hour format with AM/PM

    formatted = localized_value.strftime(format_string)

    if arg == 'with_tz':
        tz_abbrev = localized_value.tzinfo.tzname(localized_value)
        formatted += f" ({tz_abbrev})"

    return formatted

@register.filter
def sync_status_display(value):
    if value is None:
        return ''

    if value == 'synced':
        return 'Synced!'
    elif value == 'syncing':
        return 'Syncing...'
    else:
        return 'Error'

@register.filter
def parse_spoilers(text):
    """
    Convert Discord-style spoiler tags ||text|| into clickable HTML spoilers.

    Example: ||This is a spoiler|| becomes a clickable spoiler element.
    """
    if not text:
        return text

    # SAFETY: escape() is called FIRST on the entire input, converting all HTML
    # special characters to entities (< > & " '). The regex then operates on the
    # already-escaped text. The captured spoiler content is already entity-encoded,
    # so injecting it into the HTML output is safe (no raw user HTML reaches the DOM).
    # mark_safe() is valid here because ALL user content was escaped before processing.
    escaped_text = escape(text)

    # Then replace ||spoiler|| tags with HTML
    # Pattern matches ||anything|| but uses non-greedy matching
    pattern = r'\|\|(.+?)\|\|'

    def replace_spoiler(match):
        spoiler_content = match.group(1)
        # Create a clickable spoiler element with DaisyUI styling
        return (
            f'<span class="spoiler inline-block cursor-pointer select-none bg-base-content text-base-content '
            f'transition-colors duration-200 rounded px-1" '
            f'onclick="this.classList.toggle(\'bg-base-content\'); this.classList.toggle(\'text-base-content\'); '
            f'this.classList.toggle(\'bg-base-200\'); this.classList.toggle(\'text-base-content/90\');" '
            f'title="Click to reveal/hide spoiler">{spoiler_content}</span>'
        )

    result = re.sub(pattern, replace_spoiler, escaped_text)
    return mark_safe(result)

@register.filter
def moderator_display_name(user):
    """
    Get display name for a moderator (staff user).

    Returns the linked PSN username if available, otherwise returns the user's username.

    Args:
        user: CustomUser instance

    Returns:
        str: Display name for the moderator
    """
    if not user:
        return 'Unknown'

    # Try to get PSN username from linked profile
    if hasattr(user, 'profile') and user.profile:
        return user.profile.display_psn_username or user.profile.psn_username

    # Fall back to Django username
    return user.username or user.email


@register.filter
def tojson(value):
    """
    Convert a Python object to JSON string for use in HTML data attributes.
    Use with single-quoted attributes: data-attr='{{ value|tojson }}'

    Returns mark_safe output with HTML-critical characters escaped to
    prevent XSS while preserving valid JSON structure.
    """
    try:
        result = json.dumps(value)
    except (TypeError, ValueError):
        return mark_safe('[]')
    # Escape characters that could break out of HTML context:
    # < > prevent </script> injection, & prevents entity confusion,
    # ' prevents breaking out of single-quoted attributes
    result = result.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace("'", '\\u0027')
    return mark_safe(result)


@register.simple_tag
def gradient_themes_json():
    """
    Output the gradient themes registry as JSON for JavaScript consumption.

    Used to provide a single source of truth for theme definitions.
    JavaScript can access themes via window.GRADIENT_THEMES after including
    this in a script tag.

    Usage in template:
        <script>
            window.GRADIENT_THEMES = {% gradient_themes_json %};
        </script>
    """
    # Deferred import to avoid circular imports (templatetags loading before models)
    from trophies.themes import get_themes_for_js

    return mark_safe(json.dumps(get_themes_for_js()))