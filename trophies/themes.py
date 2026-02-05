"""
Premium Background Themes Registry

This module defines gradient background themes available for premium users.
These themes are used for site-wide backgrounds and share image generation.

This is the single source of truth for theme definitions - the JavaScript
in share-image.js reads from window.GRADIENT_THEMES which is populated
from this registry.
"""

# Theme definitions
# Each theme has: name, description, accent_color, background (CSS gradient)
# Some themes have additional properties like background_size, background_position
GRADIENT_THEMES = {
    'apocalypticMoss': {
        'name': 'Apocalyptic Moss',
        'description': 'Post-apocalyptic greens',
        'accent_color': '#4a7c59',
        'background': '''
            radial-gradient(ellipse at 20% 30%, rgba(74, 124, 89, 0.3) 0%, transparent 50%),
            radial-gradient(ellipse at 80% 70%, rgba(101, 67, 33, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 50% 80%, rgba(74, 124, 89, 0.2) 0%, transparent 50%),
            linear-gradient(to bottom, #0f1512 0%, #1a1f1c 50%, #0f1512 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(74, 124, 89, 0.25), rgba(101, 67, 33, 0.15))',
        'banner_border_color': '#4a7c59'
    },
    'carbonFiber': {
        'name': 'Carbon Fiber',
        'description': 'Sleek modern subtle',
        'accent_color': '#8ba3b8',
        'background': '''
            repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(0, 0, 0, 0.1) 2px, rgba(0, 0, 0, 0.1) 4px),
            linear-gradient(135deg, #1a1b1f 0%, #0f1012 50%, #1a1b1f 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(139, 163, 184, 0.15), rgba(139, 163, 184, 0.05))',
        'banner_border_color': '#8ba3b8'
    },
    'cornerSpotlights': {
        'name': 'Corner Glow',
        'description': 'Glowing corners effect',
        'accent_color': '#67d1f8',
        'background': '''
            radial-gradient(ellipse at 0% 0%, rgba(103, 209, 248, 0.4) 0%, transparent 40%),
            radial-gradient(ellipse at 100% 100%, rgba(149, 128, 255, 0.4) 0%, transparent 40%),
            linear-gradient(135deg, #1a1b1f 0%, #2a2e34 50%, #1a1b1f 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.25), rgba(103, 209, 248, 0.1))',
        'banner_border_color': '#67d1f8'
    },
    'cosmicNebula': {
        'name': 'Cosmic Nebula',
        'description': 'Space galaxy theme',
        'accent_color': '#ff6bdb',
        'background': '''
            radial-gradient(ellipse at 25% 25%, rgba(149, 128, 255, 0.35) 0%, transparent 45%),
            radial-gradient(ellipse at 75% 35%, rgba(255, 107, 219, 0.3) 0%, transparent 50%),
            radial-gradient(ellipse at 50% 75%, rgba(103, 209, 248, 0.25) 0%, transparent 55%),
            radial-gradient(ellipse at 15% 80%, rgba(149, 128, 255, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 85% 85%, rgba(255, 107, 219, 0.2) 0%, transparent 45%),
            linear-gradient(to bottom, #0a0515 0%, #1a1b2e 50%, #0a0515 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 107, 219, 0.25), rgba(149, 128, 255, 0.15))',
        'banner_border_color': '#ff6bdb'
    },
    'default': {
        'name': 'Default',
        'description': 'Subtle diagonal gradient',
        'accent_color': '#67d1f8',
        'background': 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.15), rgba(103, 209, 248, 0.05))',
        'banner_border_color': '#67d1f8'
    },
    'dragonFlame': {
        'name': 'Dragon Flame',
        'description': 'Purple dragon fire',
        'accent_color': '#a855f7',
        'background': '''
            radial-gradient(ellipse at 60% 30%, rgba(168, 85, 247, 0.4) 0%, transparent 50%),
            radial-gradient(ellipse at 20% 70%, rgba(255, 170, 92, 0.35) 0%, transparent 45%),
            radial-gradient(ellipse at 80% 80%, rgba(168, 85, 247, 0.25) 0%, transparent 50%),
            linear-gradient(to bottom, #1a0d2e 0%, #0f0618 50%, #1a0d2e 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(168, 85, 247, 0.3), rgba(255, 170, 92, 0.2))',
        'banner_border_color': '#a855f7'
    },
    'electricStorm': {
        'name': 'Electric Storm',
        'description': 'High energy dramatic',
        'accent_color': '#67d1f8',
        'background': '''
            radial-gradient(ellipse at 15% 20%, rgba(103, 209, 248, 0.4) 0%, transparent 30%),
            radial-gradient(ellipse at 85% 30%, rgba(149, 128, 255, 0.35) 0%, transparent 35%),
            radial-gradient(ellipse at 50% 80%, rgba(103, 209, 248, 0.3) 0%, transparent 40%),
            radial-gradient(ellipse at 30% 60%, rgba(149, 128, 255, 0.2) 0%, transparent 30%),
            linear-gradient(to bottom, #0f0f1a 0%, #1a1b1f 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.3), rgba(149, 128, 255, 0.2))',
        'banner_border_color': '#67d1f8'
    },
    'emeraldMatrix': {
        'name': 'Emerald Matrix',
        'description': 'Digital tech aesthetic',
        'accent_color': '#67f8c8',
        'background': '''
            radial-gradient(ellipse at 10% 30%, rgba(103, 248, 200, 0.25) 0%, transparent 50%),
            radial-gradient(ellipse at 90% 70%, rgba(103, 248, 200, 0.15) 0%, transparent 50%),
            linear-gradient(180deg, #0a1a14 0%, #050f0a 50%, #0a1a14 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(103, 248, 200, 0.25), rgba(103, 248, 200, 0.08))',
        'banner_border_color': '#67f8c8'
    },
    'gameArtBlur': {
        'name': 'Game Art',
        'description': 'Blurred game cover background',
        'accent_color': '#67d1f8',
        'background': 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)',  # Fallback
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.2), rgba(103, 209, 248, 0.08))',
        'banner_border_color': '#67d1f8',
        'requires_game_image': True,  # Special flag for JS to handle
        'game_image_source': 'game_image'  # Uses game cover image
    },
    'gameArtConceptBg': {
        'name': 'Game Art (Wide)',
        'description': 'Wide concept background image',
        'accent_color': '#67d1f8',
        'background': 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)',  # Fallback
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.2), rgba(103, 209, 248, 0.08))',
        'banner_border_color': '#67d1f8',
        'requires_game_image': True,
        'game_image_source': 'concept_bg_url'  # Uses concept background image
    },
    'gradientMesh': {
        'name': 'Gradient Mesh',
        'description': 'Colorful overlapping gradients',
        'accent_color': '#67d1f8',
        'background': '''
            radial-gradient(ellipse at 20% 20%, rgba(103, 209, 248, 0.3) 0%, transparent 50%),
            radial-gradient(ellipse at 80% 80%, rgba(149, 128, 255, 0.3) 0%, transparent 50%),
            radial-gradient(ellipse at 80% 20%, rgba(255, 170, 92, 0.15) 0%, transparent 40%),
            linear-gradient(to bottom right, #2a2e34, #1a1b1f, #2a2e34)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.2), rgba(149, 128, 255, 0.15))',
        'banner_border_color': '#67d1f8'
    },
    'inferiority': {
        'name': 'Inferiority',
        'description': 'For Xbox sympathizers',
        'accent_color': '#107c10',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(16, 124, 16, 0.2) 0%, transparent 60%),
            linear-gradient(135deg, #0a0f0a 0%, #1a1f1a 50%, #0a0f0a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(16, 124, 16, 0.15), rgba(16, 124, 16, 0.05))',
        'banner_border_color': '#107c10'
    },
    'jungleMarsupial': {
        'name': 'Jungle Marsupial',
        'description': 'Orange jungle energy',
        'accent_color': '#ff8c00',
        'background': '''
            radial-gradient(ellipse at 40% 40%, rgba(255, 140, 0, 0.35) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 20%, rgba(34, 139, 34, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 20% 80%, rgba(255, 140, 0, 0.25) 0%, transparent 40%),
            linear-gradient(135deg, #1a1510 0%, #0f0f0a 50%, #1a1510 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 140, 0, 0.3), rgba(34, 139, 34, 0.2))',
        'banner_border_color': '#ff8c00'
    },
    'logoBackdrop': {
        'name': 'Logo Backdrop',
        'description': 'PlatPursuit branding',
        'accent_color': '#67d1f8',
        'background': '''
            linear-gradient(rgba(26, 27, 31, 0.88), rgba(26, 27, 31, 0.88)),
            url("/static/images/logo.png")
        ''',
        'background_size': 'auto, 120px',
        'background_position': 'center, center',
        'background_repeat': 'repeat, repeat',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.2), rgba(103, 209, 248, 0.08))',
        'banner_border_color': '#67d1f8'
    },
    'machineHunter': {
        'name': 'Machine Hunter',
        'description': 'Tribal tech sunrise',
        'accent_color': '#ff6b35',
        'background': '''
            radial-gradient(ellipse at 50% 100%, rgba(255, 107, 53, 0.4) 0%, rgba(255, 140, 0, 0.25) 35%, transparent 65%),
            radial-gradient(ellipse at 30% 30%, rgba(0, 191, 255, 0.2) 0%, transparent 40%),
            linear-gradient(0deg, #2a1810 0%, #1a1520 50%, #0a0f1a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 107, 53, 0.3), rgba(0, 191, 255, 0.15))',
        'banner_border_color': '#ff6b35'
    },
    'midnightCrimson': {
        'name': 'Midnight Crimson',
        'description': 'Bold and rare',
        'accent_color': '#ff5757',
        'background': '''
            radial-gradient(ellipse at 20% 20%, rgba(255, 87, 87, 0.3) 0%, transparent 50%),
            radial-gradient(ellipse at 80% 80%, rgba(200, 50, 50, 0.25) 0%, transparent 50%),
            linear-gradient(135deg, #1a0f0f 0%, #2a1a1a 50%, #1a0f0f 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 87, 87, 0.25), rgba(255, 87, 87, 0.08))',
        'banner_border_color': '#ff5757'
    },
    'minimalDark': {
        'name': 'Minimal Dark',
        'description': 'Clean dark with vignette',
        'accent_color': '#67d1f8',
        'background': 'radial-gradient(ellipse at center, #2a2e34 0%, #1a1b1f 70%, #0f1012 100%)',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.12), rgba(103, 209, 248, 0.04))',
        'banner_border_color': '#67d1f8'
    },
    'neonPurple': {
        'name': 'Neon Purple',
        'description': 'Vibrant purple atmosphere',
        'accent_color': '#9580ff',
        'background': '''
            radial-gradient(ellipse at 30% 0%, rgba(149, 128, 255, 0.4) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 100%, rgba(103, 209, 248, 0.2) 0%, transparent 50%),
            linear-gradient(180deg, #1a1b2e 0%, #0f0f1a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(149, 128, 255, 0.25), rgba(149, 128, 255, 0.08))',
        'banner_border_color': '#9580ff'
    },
    'platinumShimmer': {
        'name': 'Platinum Shimmer',
        'description': 'Metallic prestigious',
        'accent_color': '#e8e8e8',
        'background': '''
            radial-gradient(ellipse at 50% 30%, rgba(232, 232, 232, 0.15) 0%, transparent 60%),
            radial-gradient(ellipse at 30% 70%, rgba(200, 200, 200, 0.12) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 60%, rgba(180, 180, 180, 0.1) 0%, transparent 45%),
            linear-gradient(135deg, #1a1b1f 0%, #2a2e34 50%, #1a1b1f 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(232, 232, 232, 0.2), rgba(232, 232, 232, 0.08))',
        'banner_border_color': '#e8e8e8'
    },
    'playstationBlue': {
        'name': 'PlayStation Blue',
        'description': 'Classic blue sweep',
        'accent_color': '#4a9eff',
        'background': 'linear-gradient(135deg, #0d1b2a 0%, #1b3a5f 30%, #0d1b2a 60%, #1a1b1f 100%)',
        'banner_background': 'linear-gradient(to right, rgba(74, 158, 255, 0.25), rgba(74, 158, 255, 0.08))',
        'banner_border_color': '#4a9eff'
    },
    'rainbowRoad': {
        'name': 'Rainbow Road',
        'description': 'Wait wrong console...',
        'accent_color': '#ff6bdb',
        'background': '''
            linear-gradient(90deg,
                rgba(255, 0, 0, 0.15) 0%,
                rgba(255, 127, 0, 0.15) 14%,
                rgba(255, 255, 0, 0.15) 28%,
                rgba(0, 255, 0, 0.15) 42%,
                rgba(0, 127, 255, 0.15) 57%,
                rgba(139, 0, 255, 0.15) 71%,
                rgba(255, 0, 255, 0.15) 85%,
                rgba(255, 0, 0, 0.15) 100%
            ),
            radial-gradient(ellipse at center, #2a2e34 0%, #1a1b1f 70%, #0f1012 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 0, 255, 0.25), rgba(0, 127, 255, 0.2))',
        'banner_border_color': '#ff6bdb'
    },
    'retroWave': {
        'name': 'Retro Wave',
        'description': 'Synthwave vaporwave',
        'accent_color': '#ff6bdb',
        'background': '''
            radial-gradient(ellipse at 50% 120%, rgba(255, 20, 147, 0.5) 0%, rgba(138, 43, 226, 0.3) 40%, transparent 70%),
            radial-gradient(ellipse at 50% -20%, rgba(0, 191, 255, 0.4) 0%, rgba(138, 43, 226, 0.2) 40%, transparent 70%),
            repeating-linear-gradient(0deg, transparent, transparent 40px, rgba(255, 20, 147, 0.03) 40px, rgba(255, 20, 147, 0.03) 42px),
            linear-gradient(180deg, #0d0221 0%, #1a0933 30%, #2d1b4e 50%, #1a0933 70%, #0d0221 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 20, 147, 0.35), rgba(0, 191, 255, 0.25))',
        'banner_border_color': '#ff14c8'
    },
    'sunsetGold': {
        'name': 'Sunset Gold',
        'description': 'Warm golden tones',
        'accent_color': '#ffaa5c',
        'background': '''
            radial-gradient(ellipse at 70% 20%, rgba(255, 170, 92, 0.35) 0%, transparent 50%),
            radial-gradient(ellipse at 20% 80%, rgba(255, 136, 51, 0.2) 0%, transparent 50%),
            linear-gradient(to bottom, #2a2520 0%, #1a1815 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 170, 92, 0.25), rgba(255, 170, 92, 0.08))',
        'banner_border_color': '#ffaa5c'
    },
    'treasureMap': {
        'name': 'Treasure Map',
        'description': 'Parchment adventure',
        'accent_color': '#d4a574',
        'background': '''
            radial-gradient(ellipse at 40% 40%, rgba(212, 165, 116, 0.2) 0%, transparent 60%),
            radial-gradient(ellipse at 70% 70%, rgba(139, 90, 43, 0.15) 0%, transparent 50%),
            linear-gradient(135deg, #1a1510 0%, #2a2015 50%, #1a1510 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(212, 165, 116, 0.25), rgba(139, 90, 43, 0.15))',
        'banner_border_color': '#d4a574'
    },
    'trophySpotlight': {
        'name': 'Trophy Spotlight',
        'description': 'Center focus effect',
        'accent_color': '#ffe66d',
        'background': '''
            radial-gradient(ellipse at center, rgba(255, 230, 109, 0.25) 0%, rgba(255, 230, 109, 0.1) 30%, transparent 70%),
            radial-gradient(ellipse at center, #2a2e34 0%, #1a1b1f 60%, #0f1012 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 230, 109, 0.2), rgba(255, 230, 109, 0.08))',
        'banner_border_color': '#ffe66d'
    },
    'warriorRage': {
        'name': 'Warrior Rage',
        'description': 'Spartan battle fury',
        'accent_color': '#c41e3a',
        'background': '''
            radial-gradient(ellipse at 30% 20%, rgba(196, 30, 58, 0.4) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 80%, rgba(139, 0, 0, 0.35) 0%, transparent 45%),
            radial-gradient(ellipse at 50% 50%, rgba(255, 69, 0, 0.15) 0%, transparent 60%),
            linear-gradient(135deg, #1a0a0a 0%, #2a1010 40%, #1a0505 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(196, 30, 58, 0.35), rgba(139, 0, 0, 0.2))',
        'banner_border_color': '#c41e3a'
    },
}


def _clean_css(css_string):
    """Clean up CSS string by removing extra whitespace."""
    import re
    return re.sub(r'\s+', ' ', css_string).strip()


def get_theme(key):
    """
    Get a theme definition by key.

    Args:
        key: The theme key (e.g., 'cosmicNebula')

    Returns:
        dict: Theme definition or None if not found
    """
    return GRADIENT_THEMES.get(key)


def get_theme_css(key):
    """
    Get the cleaned CSS background string for a theme.

    Args:
        key: The theme key

    Returns:
        str: CSS background value ready for inline style, or empty string if not found
    """
    theme = GRADIENT_THEMES.get(key)
    if not theme:
        return ''
    return _clean_css(theme['background'])


def get_theme_style(key):
    """
    Get complete inline style string for a theme (including background-size, etc.).

    Args:
        key: The theme key

    Returns:
        str: Complete CSS style string ready for style attribute
    """
    theme = GRADIENT_THEMES.get(key)
    if not theme:
        return ''

    styles = [f"background: {_clean_css(theme['background'])}"]

    if 'background_size' in theme:
        styles.append(f"background-size: {theme['background_size']}")

    if 'background_position' in theme:
        styles.append(f"background-position: {theme['background_position']}")

    if 'background_repeat' in theme:
        styles.append(f"background-repeat: {theme['background_repeat']}")

    # Add fixed attachment for site backgrounds
    styles.append("background-attachment: fixed")

    return '; '.join(styles)


# Generate sorted choices for Django form fields
# Default first, then alphabetical by name
def _generate_theme_choices():
    """Generate sorted theme choices for form fields."""
    choices = [('', 'None')]

    # Get all themes except default
    other_themes = [(key, data['name']) for key, data in GRADIENT_THEMES.items()
                    if key != 'default' and not data.get('requires_game_image')]

    # Sort alphabetically by name
    other_themes.sort(key=lambda x: x[1])

    # Add default first if it exists and doesn't require game image
    if 'default' in GRADIENT_THEMES and not GRADIENT_THEMES['default'].get('requires_game_image'):
        choices.append(('default', GRADIENT_THEMES['default']['name']))

    # Add the rest
    choices.extend(other_themes)

    return choices


THEME_CHOICES = _generate_theme_choices()


def get_themes_for_js():
    """
    Get themes formatted for JavaScript consumption.

    Returns:
        dict: Theme definitions formatted for JS (camelCase keys, cleaned CSS)
    """
    js_themes = {}
    for key, theme in GRADIENT_THEMES.items():
        js_themes[key] = {
            'name': theme['name'],
            'description': theme['description'],
            'accentColor': theme['accent_color'],
            'background': _clean_css(theme['background']),
            'bannerBackground': theme['banner_background'],
            'bannerBorderColor': theme['banner_border_color'],
        }

        # Add optional properties
        if 'background_size' in theme:
            js_themes[key]['backgroundSize'] = theme['background_size']
        if 'background_position' in theme:
            js_themes[key]['backgroundPosition'] = theme['background_position']
        if 'background_repeat' in theme:
            js_themes[key]['backgroundRepeat'] = theme['background_repeat']
        if theme.get('requires_game_image'):
            js_themes[key]['requiresGameImage'] = True
        if theme.get('game_image_source'):
            js_themes[key]['gameImageSource'] = theme['game_image_source']

    return js_themes


def get_available_themes_for_grid(include_game_art=False):
    """
    Get themes formatted for the color grid template.

    Args:
        include_game_art: If True, includes themes with requires_game_image=True

    Returns:
        list: List of (key, theme_data) tuples for template, sorted with default first
    """
    themes = []
    for key, data in sorted(GRADIENT_THEMES.items(),
                            key=lambda x: (x[0] != 'default', x[1]['name'])):
        requires_game_image = data.get('requires_game_image', False)

        # Skip game art themes unless explicitly requested
        if requires_game_image and not include_game_art:
            continue

        theme_entry = {
            'name': data['name'],
            'description': data['description'],
            'background_css': _clean_css(data['background']),
        }

        # For game art themes, add extra properties for JS to use
        if requires_game_image:
            theme_entry['is_game_art'] = True
            theme_entry['game_image_source'] = data.get('game_image_source', 'game_image')

        themes.append((key, theme_entry))

    return themes
