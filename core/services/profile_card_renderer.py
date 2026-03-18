"""
Pre-rendering pipeline for profile card forum signatures (PNG + SVG).

Renders forum sigs ahead of time so the public endpoint only serves flat files,
keeping Playwright out of the anonymous request path entirely.
"""
import logging
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from core.services.profile_card_service import ProfileCardDataService

logger = logging.getLogger(__name__)

# Directory for pre-rendered profile signature images
PROFILE_SIGS_DIR = Path(settings.BASE_DIR) / 'profile_sigs'


def _ensure_sigs_dir():
    """Create the profile_sigs directory if it doesn't exist."""
    PROFILE_SIGS_DIR.mkdir(exist_ok=True)


def _fetch_avatar_base64(avatar_url):
    """Fetch avatar image and return as base64 data URI for SVG embedding."""
    if not avatar_url:
        return ''
    try:
        from core.services.share_image_cache import ShareImageCache, SHARE_TEMP_DIR
        serve_path = ShareImageCache.fetch_and_cache(avatar_url)
        if not serve_path:
            return ''
        filename = serve_path.split('/')[-1]
        file_path = SHARE_TEMP_DIR / filename
        if file_path.exists():
            return ShareImageCache.local_file_to_base64(str(file_path))
        return ''
    except Exception:
        logger.exception('Failed to fetch avatar as base64')
        return ''


def _fetch_logo_base64():
    """Get the PlatPursuit logo as base64 data URI."""
    try:
        from django.contrib.staticfiles.finders import find as static_find
        from core.services.share_image_cache import ShareImageCache
        logo_path = static_find('images/logo.png')
        if logo_path:
            return ShareImageCache.local_file_to_base64(logo_path)
        return ''
    except Exception:
        logger.exception('Failed to fetch logo as base64')
        return ''


def render_sig_png(profile):
    """
    Render a forum signature PNG for a profile.

    Compares the data hash against the last render to skip unnecessary
    re-renders. Returns the output file path, or None on failure.
    """
    from trophies.models import ProfileCardSettings
    from core.services.share_image_cache import ShareImageCache

    _ensure_sigs_dir()

    # Ensure settings exist
    settings_obj, _ = ProfileCardSettings.objects.get_or_create(profile=profile)
    token = str(settings_obj.public_sig_token)

    # Gather data
    data = ProfileCardDataService.get_profile_card_data(profile)
    data_hash = ProfileCardDataService.compute_data_hash(data)

    # Skip if unchanged
    if settings_obj.sig_render_hash == data_hash:
        existing = PROFILE_SIGS_DIR / f"{token}.png"
        if existing.exists():
            logger.info(f"[SIG-PNG] Skipping unchanged sig for {profile.psn_username}")
            return str(existing)

    # Cache avatar for Playwright (serve path, not base64)
    avatar_serve = ShareImageCache.fetch_and_cache(data['avatar_url'])

    # Build template context (pass full data for design parity with landscape card)
    context = {
        'format': 'signature',
        'psn_username': data['psn_username'],
        'avatar_url': avatar_serve or '',
        'flag': data['flag'],
        'is_plus': data['is_plus'],
        'displayed_title': data['displayed_title'],
        'trophy_level': data['trophy_level'],
        'total_plats': data['total_plats'],
        'total_golds': data['total_golds'],
        'total_silvers': data['total_silvers'],
        'total_bronzes': data['total_bronzes'],
        'total_earned': data['total_earned'],
        'total_games': data['total_games'],
        'total_badge_xp': data['total_badge_xp'],
        'xp_rank': data['xp_rank'],
        'xp_total_users': data['xp_total_users'],
        'country_xp_rank': data['country_xp_rank'],
        'country_xp_total': data['country_xp_total'],
        'country_code': data['country_code'],
        'avg_progress': data['avg_progress'],
        'earn_rate': data['earn_rate'],
        'total_completes': data['total_completes'],
        'badge_name': data['badge_name'],
        'badge_image_url': data.get('badge_image_url', ''),
        'pct_plats': data['pct_plats'],
        'pct_golds': data['pct_golds'],
        'pct_silvers': data['pct_silvers'],
        'pct_bronzes': data['pct_bronzes'],
    }

    html = render_to_string(
        'shareables/partials/profile_sig_card.html', context
    )

    try:
        from core.services.playwright_renderer import render_png
        png_bytes = render_png(html, format_type='signature', theme_key='default')
    except Exception:
        logger.exception(f"[SIG-PNG] Playwright render failed for {profile.psn_username}")
        return None

    # Write to disk
    output_path = PROFILE_SIGS_DIR / f"{token}.png"
    output_path.write_bytes(png_bytes)

    # Update metadata
    settings_obj.sig_last_rendered = timezone.now()
    settings_obj.sig_render_hash = data_hash
    settings_obj.save(update_fields=['sig_last_rendered', 'sig_render_hash'])

    logger.info(
        f"[SIG-PNG] Rendered sig for {profile.psn_username} "
        f"({len(png_bytes)} bytes)"
    )
    return str(output_path)


def render_sig_svg(profile):
    """
    Render a forum signature SVG for a profile.

    SVG rendering is cheap (template render, no Playwright), but we still
    pre-render and cache to disk to avoid DB queries on every public request.
    """
    from trophies.models import ProfileCardSettings

    _ensure_sigs_dir()

    settings_obj, _ = ProfileCardSettings.objects.get_or_create(profile=profile)
    token = str(settings_obj.public_sig_token)

    # Gather data
    data = ProfileCardDataService.get_profile_card_data(profile)

    # For SVG, we need base64-encoded images (self-contained, no external refs)
    avatar_base64 = _fetch_avatar_base64(data['avatar_url'])
    logo_base64 = _fetch_logo_base64()

    # Truncate title for SVG layout (no text wrapping in SVG)
    title = data['displayed_title']
    if title and len(title) > 30:
        title = title[:28] + '...'

    context = {
        'psn_username': data['psn_username'],
        'avatar_base64': avatar_base64,
        'logo_base64': logo_base64,
        'flag': data['flag'],
        'is_plus': data['is_plus'],
        'displayed_title': title,
        'trophy_level': data['trophy_level'],
        'total_plats': data['total_plats'],
        'total_golds': data['total_golds'],
        'total_silvers': data['total_silvers'],
        'total_bronzes': data['total_bronzes'],
        'total_earned': data['total_earned'],
        'total_games': data['total_games'],
        'total_badge_xp': data['total_badge_xp'],
        'xp_rank': data['xp_rank'],
        'xp_total_users': data['xp_total_users'],
        'country_xp_rank': data['country_xp_rank'],
        'country_xp_total': data['country_xp_total'],
        'country_code': data['country_code'],
        'avg_progress': data['avg_progress'],
        'earn_rate': data['earn_rate'],
        'total_completes': data['total_completes'],
        'badge_name': data['badge_name'],
        'completion_pct': min(data['avg_progress'], 100.0),
        'pct_plats': data['pct_plats'],
        'pct_golds': data['pct_golds'],
        'pct_silvers': data['pct_silvers'],
        'pct_bronzes': data['pct_bronzes'],
    }

    svg_content = render_to_string(
        'shareables/partials/profile_sig_card.svg', context
    )

    # Write to disk
    output_path = PROFILE_SIGS_DIR / f"{token}.svg"
    output_path.write_text(svg_content, encoding='utf-8')

    logger.info(f"[SIG-SVG] Rendered sig for {profile.psn_username}")
    return str(output_path)


def render_all_sigs(profile):
    """Render both PNG and SVG sigs for a profile."""
    png_path = render_sig_png(profile)
    svg_path = render_sig_svg(profile)
    return png_path, svg_path


def cleanup_orphaned_sigs():
    """Remove sig files for tokens that no longer exist or are disabled."""
    from trophies.models import ProfileCardSettings

    _ensure_sigs_dir()

    # Get all active tokens
    active_tokens = set(
        ProfileCardSettings.objects
        .filter(public_sig_enabled=True)
        .values_list('public_sig_token', flat=True)
    )
    active_filenames = set()
    for token in active_tokens:
        active_filenames.add(f"{token}.png")
        active_filenames.add(f"{token}.svg")

    removed = 0
    for path in PROFILE_SIGS_DIR.iterdir():
        if path.name not in active_filenames:
            path.unlink(missing_ok=True)
            removed += 1

    if removed:
        logger.info(f"[SIG-CLEANUP] Removed {removed} orphaned sig files")
    return removed
