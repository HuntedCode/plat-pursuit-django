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
        'banner_border_color': '#4a7c59',
        'category': 'nature'
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
        'banner_border_color': '#8ba3b8',
        'category': 'dark_moody'
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
        'banner_border_color': '#67d1f8',
        'category': 'general'
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
        'banner_border_color': '#ff6bdb',
        'category': 'vibrant'
    },
    'default': {
        'name': 'Default',
        'description': 'Subtle diagonal gradient',
        'accent_color': '#67d1f8',
        'background': 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.15), rgba(103, 209, 248, 0.05))',
        'banner_border_color': '#67d1f8',
        'category': 'general'
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
        'banner_border_color': '#a855f7',
        'category': 'vibrant'
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
        'banner_border_color': '#67d1f8',
        'category': 'vibrant'
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
        'banner_border_color': '#67f8c8',
        'category': 'nature'
    },
    'gameArtBlur': {
        'name': 'Game Art',
        'description': 'Blurred game cover background',
        'accent_color': '#67d1f8',
        'background': 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)',  # Fallback
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.2), rgba(103, 209, 248, 0.08))',
        'banner_border_color': '#67d1f8',
        'category': 'special',
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
        'category': 'special',
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
        'banner_border_color': '#67d1f8',
        'category': 'general'
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
        'banner_border_color': '#107c10',
        'category': 'playstation'
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
        'banner_border_color': '#ff8c00',
        'category': 'playstation'
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
        'banner_border_color': '#67d1f8',
        'category': 'special'
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
        'banner_border_color': '#ff6b35',
        'category': 'playstation'
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
        'banner_border_color': '#ff5757',
        'category': 'dark_moody'
    },
    'minimalDark': {
        'name': 'Minimal Dark',
        'description': 'Clean dark with vignette',
        'accent_color': '#67d1f8',
        'background': 'radial-gradient(ellipse at center, #2a2e34 0%, #1a1b1f 70%, #0f1012 100%)',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.12), rgba(103, 209, 248, 0.04))',
        'banner_border_color': '#67d1f8',
        'category': 'dark_moody'
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
        'banner_border_color': '#9580ff',
        'category': 'vibrant'
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
        'banner_border_color': '#e8e8e8',
        'category': 'metallic'
    },
    'playstationBlue': {
        'name': 'PlayStation Blue',
        'description': 'Classic blue sweep',
        'accent_color': '#4a9eff',
        'background': 'linear-gradient(135deg, #0d1b2a 0%, #1b3a5f 30%, #0d1b2a 60%, #1a1b1f 100%)',
        'banner_background': 'linear-gradient(to right, rgba(74, 158, 255, 0.25), rgba(74, 158, 255, 0.08))',
        'banner_border_color': '#4a9eff',
        'category': 'playstation'
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
        'banner_border_color': '#ff6bdb',
        'category': 'vibrant'
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
        'banner_border_color': '#ff14c8',
        'category': 'retro'
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
        'banner_border_color': '#ffaa5c',
        'category': 'nature'
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
        'banner_border_color': '#d4a574',
        'category': 'nature'
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
        'banner_border_color': '#ffe66d',
        'category': 'general'
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
        'banner_border_color': '#c41e3a',
        'category': 'playstation'
    },

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DARK & MOODY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    'abyssalDepth': {
        'name': 'Abyssal Depth',
        'description': 'The deep dark below',
        'accent_color': '#1a3a5c',
        'background': '''
            radial-gradient(ellipse at 50% 90%, rgba(26, 58, 92, 0.3) 0%, transparent 50%),
            radial-gradient(ellipse at 50% 50%, rgba(10, 20, 40, 0.5) 0%, transparent 70%),
            linear-gradient(180deg, #050508 0%, #0a0f1a 40%, #050508 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(26, 58, 92, 0.2), rgba(26, 58, 92, 0.05))',
        'banner_border_color': '#1a3a5c',
        'category': 'dark_moody'
    },
    'blackout': {
        'name': 'Blackout',
        'description': 'Near-total darkness',
        'accent_color': '#333333',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(30, 30, 30, 0.5) 0%, transparent 60%),
            linear-gradient(180deg, #080808 0%, #0a0a0a 50%, #060606 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(50, 50, 50, 0.15), rgba(50, 50, 50, 0.05))',
        'banner_border_color': '#333333',
        'category': 'dark_moody'
    },
    'bruisedSteel': {
        'name': 'Bruised Steel',
        'description': 'Worn industrial metal',
        'accent_color': '#6b7b8d',
        'background': '''
            radial-gradient(ellipse at 30% 40%, rgba(107, 123, 141, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 60%, rgba(80, 90, 105, 0.15) 0%, transparent 50%),
            linear-gradient(135deg, #12141a 0%, #1a1e26 50%, #12141a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(107, 123, 141, 0.2), rgba(107, 123, 141, 0.05))',
        'banner_border_color': '#6b7b8d',
        'category': 'dark_moody'
    },
    'darkEmber': {
        'name': 'Dark Ember',
        'description': 'Dying coals in the dark',
        'accent_color': '#8b3a1a',
        'background': '''
            radial-gradient(ellipse at 60% 80%, rgba(139, 58, 26, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 30% 60%, rgba(100, 30, 10, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #0a0605 0%, #120a08 50%, #080504 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(139, 58, 26, 0.2), rgba(139, 58, 26, 0.05))',
        'banner_border_color': '#8b3a1a',
        'category': 'dark_moody'
    },
    'eclipseShadow': {
        'name': 'Eclipse Shadow',
        'description': 'Solar eclipse corona',
        'accent_color': '#c4a35a',
        'background': '''
            radial-gradient(circle at 50% 40%, rgba(196, 163, 90, 0.15) 0%, rgba(196, 163, 90, 0.05) 25%, transparent 50%),
            radial-gradient(circle at 50% 40%, rgba(0, 0, 0, 0.9) 0%, transparent 30%),
            linear-gradient(180deg, #08080a 0%, #0f0f14 50%, #08080a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(196, 163, 90, 0.2), rgba(196, 163, 90, 0.05))',
        'banner_border_color': '#c4a35a',
        'category': 'dark_moody'
    },
    'hollowKnight': {
        'name': 'Hollow Depths',
        'description': 'Underground blue-grey caverns',
        'accent_color': '#5b8fa8',
        'background': '''
            radial-gradient(ellipse at 50% 100%, rgba(91, 143, 168, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 20% 30%, rgba(60, 80, 100, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #0a0e12 0%, #0f1520 60%, #0a0c10 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(91, 143, 168, 0.2), rgba(91, 143, 168, 0.05))',
        'banner_border_color': '#5b8fa8',
        'category': 'dark_moody'
    },
    'inkWash': {
        'name': 'Ink Wash',
        'description': 'Japanese sumi-e darkness',
        'accent_color': '#4a4a5a',
        'background': '''
            radial-gradient(ellipse at 70% 30%, rgba(74, 74, 90, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(50, 50, 65, 0.15) 0%, transparent 45%),
            linear-gradient(135deg, #0a0a0e 0%, #14141a 30%, #0e0e14 60%, #0a0a0e 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(74, 74, 90, 0.15), rgba(74, 74, 90, 0.05))',
        'banner_border_color': '#4a4a5a',
        'category': 'dark_moody'
    },
    'midnightOil': {
        'name': 'Midnight Oil',
        'description': 'Late night grinding',
        'accent_color': '#2a4a6b',
        'background': '''
            radial-gradient(ellipse at 80% 20%, rgba(42, 74, 107, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 20% 80%, rgba(30, 50, 80, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #080a10 0%, #0d1018 50%, #080a10 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(42, 74, 107, 0.2), rgba(42, 74, 107, 0.05))',
        'banner_border_color': '#2a4a6b',
        'category': 'dark_moody'
    },
    'obsidianGlass': {
        'name': 'Obsidian Glass',
        'description': 'Polished volcanic glass',
        'accent_color': '#3d3d4a',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(61, 61, 74, 0.3) 0%, transparent 60%),
            radial-gradient(ellipse at 20% 20%, rgba(40, 40, 55, 0.15) 0%, transparent 40%),
            linear-gradient(135deg, #0c0c10 0%, #161620 40%, #0c0c10 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(61, 61, 74, 0.15), rgba(61, 61, 74, 0.05))',
        'banner_border_color': '#3d3d4a',
        'category': 'dark_moody'
    },
    'phantomSmoke': {
        'name': 'Phantom Smoke',
        'description': 'Wisps of dark fog',
        'accent_color': '#5a5a6a',
        'background': '''
            radial-gradient(ellipse at 20% 50%, rgba(90, 90, 106, 0.15) 0%, transparent 50%),
            radial-gradient(ellipse at 80% 40%, rgba(70, 70, 85, 0.12) 0%, transparent 45%),
            radial-gradient(ellipse at 50% 90%, rgba(60, 60, 75, 0.1) 0%, transparent 40%),
            linear-gradient(180deg, #0e0e12 0%, #141418 50%, #0a0a0e 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(90, 90, 106, 0.15), rgba(90, 90, 106, 0.05))',
        'banner_border_color': '#5a5a6a',
        'category': 'dark_moody'
    },
    'shadowedViolet': {
        'name': 'Shadowed Violet',
        'description': 'Deep purple undertones',
        'accent_color': '#4a2a6b',
        'background': '''
            radial-gradient(ellipse at 40% 30%, rgba(74, 42, 107, 0.25) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 70%, rgba(50, 20, 80, 0.2) 0%, transparent 45%),
            linear-gradient(135deg, #0a070f 0%, #120e1a 50%, #0a070f 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(74, 42, 107, 0.2), rgba(74, 42, 107, 0.05))',
        'banner_border_color': '#4a2a6b',
        'category': 'dark_moody'
    },
    'silentHill': {
        'name': 'Silent Fog',
        'description': 'Something lurks in the mist',
        'accent_color': '#7a7a6a',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(122, 122, 106, 0.12) 0%, transparent 70%),
            radial-gradient(ellipse at 30% 80%, rgba(90, 85, 75, 0.1) 0%, transparent 50%),
            linear-gradient(180deg, #10100e 0%, #1a1a16 40%, #0e0e0c 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(122, 122, 106, 0.15), rgba(122, 122, 106, 0.05))',
        'banner_border_color': '#7a7a6a',
        'category': 'dark_moody'
    },
    'soullessDark': {
        'name': 'Soulless Dark',
        'description': 'Prepare to die... of boredom',
        'accent_color': '#c8a860',
        'background': '''
            radial-gradient(ellipse at 50% 30%, rgba(200, 168, 96, 0.1) 0%, transparent 40%),
            radial-gradient(ellipse at 50% 80%, rgba(80, 60, 30, 0.12) 0%, transparent 45%),
            linear-gradient(180deg, #0a0a08 0%, #12100c 50%, #080806 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(200, 168, 96, 0.15), rgba(200, 168, 96, 0.05))',
        'banner_border_color': '#c8a860',
        'category': 'dark_moody'
    },
    'voidwalker': {
        'name': 'Voidwalker',
        'description': 'Between dimensions',
        'accent_color': '#3a1a5a',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(58, 26, 90, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 20% 80%, rgba(30, 10, 50, 0.15) 0%, transparent 40%),
            radial-gradient(ellipse at 80% 20%, rgba(40, 15, 65, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #050308 0%, #0a0610 50%, #040206 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(58, 26, 90, 0.2), rgba(58, 26, 90, 0.05))',
        'banner_border_color': '#3a1a5a',
        'category': 'dark_moody'
    },

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # VIBRANT & COLORFUL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    'auroraBoreal': {
        'name': 'Aurora Borealis',
        'description': 'Northern lights dancing',
        'accent_color': '#4af0c0',
        'background': '''
            radial-gradient(ellipse at 20% 20%, rgba(74, 240, 192, 0.3) 0%, transparent 40%),
            radial-gradient(ellipse at 60% 40%, rgba(100, 180, 255, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 80% 70%, rgba(180, 100, 255, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 40% 90%, rgba(74, 240, 192, 0.15) 0%, transparent 35%),
            linear-gradient(180deg, #0a1a15 0%, #0f1520 50%, #0a0f1a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(74, 240, 192, 0.25), rgba(100, 180, 255, 0.15))',
        'banner_border_color': '#4af0c0',
        'category': 'vibrant'
    },
    'candyPop': {
        'name': 'Candy Pop',
        'description': 'Sweet and playful',
        'accent_color': '#ff69b4',
        'background': '''
            radial-gradient(ellipse at 30% 30%, rgba(255, 105, 180, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 60%, rgba(138, 43, 226, 0.25) 0%, transparent 40%),
            radial-gradient(ellipse at 50% 90%, rgba(255, 165, 0, 0.15) 0%, transparent 35%),
            linear-gradient(135deg, #1a0f18 0%, #1f1028 50%, #1a0f18 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 105, 180, 0.3), rgba(138, 43, 226, 0.2))',
        'banner_border_color': '#ff69b4',
        'category': 'vibrant'
    },
    'cherryBlossom': {
        'name': 'Cherry Blossom',
        'description': 'Sakura petals falling',
        'accent_color': '#ff9eb5',
        'background': '''
            radial-gradient(ellipse at 30% 20%, rgba(255, 158, 181, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 70%, rgba(255, 182, 193, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 50% 50%, rgba(220, 130, 160, 0.15) 0%, transparent 50%),
            linear-gradient(180deg, #1a1015 0%, #1f141a 50%, #150e12 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 158, 181, 0.25), rgba(255, 182, 193, 0.1))',
        'banner_border_color': '#ff9eb5',
        'category': 'vibrant'
    },
    'citrusBurst': {
        'name': 'Citrus Burst',
        'description': 'Tangy orange and lime',
        'accent_color': '#ff9500',
        'background': '''
            radial-gradient(ellipse at 30% 40%, rgba(255, 149, 0, 0.35) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 60%, rgba(50, 205, 50, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 50% 20%, rgba(255, 200, 0, 0.15) 0%, transparent 35%),
            linear-gradient(135deg, #1a1508 0%, #18120a 50%, #1a1508 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 149, 0, 0.3), rgba(50, 205, 50, 0.15))',
        'banner_border_color': '#ff9500',
        'category': 'vibrant'
    },
    'electricLime': {
        'name': 'Electric Lime',
        'description': 'High voltage green',
        'accent_color': '#aaff00',
        'background': '''
            radial-gradient(ellipse at 40% 30%, rgba(170, 255, 0, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 60% 80%, rgba(100, 200, 0, 0.15) 0%, transparent 40%),
            linear-gradient(135deg, #0a1205 0%, #101a08 50%, #0a1205 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(170, 255, 0, 0.25), rgba(170, 255, 0, 0.08))',
        'banner_border_color': '#aaff00',
        'category': 'vibrant'
    },
    'fireAndIce': {
        'name': 'Fire and Ice',
        'description': 'Hot meets cold',
        'accent_color': '#ff4500',
        'background': '''
            radial-gradient(ellipse at 20% 50%, rgba(255, 69, 0, 0.35) 0%, transparent 40%),
            radial-gradient(ellipse at 80% 50%, rgba(0, 191, 255, 0.35) 0%, transparent 40%),
            radial-gradient(ellipse at 50% 50%, rgba(128, 0, 128, 0.1) 0%, transparent 50%),
            linear-gradient(90deg, #1a0a05 0%, #10101a 50%, #051a1a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 69, 0, 0.3), rgba(0, 191, 255, 0.25))',
        'banner_border_color': '#ff4500',
        'category': 'vibrant'
    },
    'holographic': {
        'name': 'Holographic',
        'description': 'Iridescent shimmer',
        'accent_color': '#c0a0ff',
        'background': '''
            radial-gradient(ellipse at 20% 30%, rgba(255, 100, 150, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 50% 60%, rgba(100, 200, 255, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 80% 30%, rgba(150, 255, 100, 0.15) 0%, transparent 35%),
            radial-gradient(ellipse at 40% 80%, rgba(192, 160, 255, 0.2) 0%, transparent 40%),
            linear-gradient(135deg, #14121a 0%, #1a1820 50%, #14121a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(192, 160, 255, 0.25), rgba(100, 200, 255, 0.15))',
        'banner_border_color': '#c0a0ff',
        'category': 'vibrant'
    },
    'laserTag': {
        'name': 'Laser Tag',
        'description': 'Neon in the dark',
        'accent_color': '#00ff88',
        'background': '''
            radial-gradient(ellipse at 10% 50%, rgba(0, 255, 136, 0.3) 0%, transparent 30%),
            radial-gradient(ellipse at 90% 30%, rgba(255, 0, 100, 0.25) 0%, transparent 30%),
            radial-gradient(ellipse at 50% 90%, rgba(0, 100, 255, 0.2) 0%, transparent 30%),
            linear-gradient(180deg, #050808 0%, #0a0a0f 50%, #050508 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(0, 255, 136, 0.25), rgba(255, 0, 100, 0.15))',
        'banner_border_color': '#00ff88',
        'category': 'vibrant'
    },
    'neonCity': {
        'name': 'Neon City',
        'description': 'Cyberpunk nightlife',
        'accent_color': '#ff00ff',
        'background': '''
            radial-gradient(ellipse at 30% 80%, rgba(255, 0, 255, 0.3) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 20%, rgba(0, 255, 255, 0.25) 0%, transparent 40%),
            radial-gradient(ellipse at 50% 50%, rgba(255, 0, 100, 0.1) 0%, transparent 50%),
            linear-gradient(180deg, #0a0510 0%, #100a18 50%, #0a0510 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 0, 255, 0.3), rgba(0, 255, 255, 0.2))',
        'banner_border_color': '#ff00ff',
        'category': 'vibrant'
    },
    'prismLight': {
        'name': 'Prism Light',
        'description': 'Light through crystal',
        'accent_color': '#88ccff',
        'background': '''
            linear-gradient(135deg,
                rgba(255, 80, 80, 0.12) 0%,
                rgba(255, 200, 80, 0.12) 20%,
                rgba(80, 255, 80, 0.12) 40%,
                rgba(80, 200, 255, 0.12) 60%,
                rgba(200, 80, 255, 0.12) 80%,
                rgba(255, 80, 150, 0.12) 100%
            ),
            linear-gradient(135deg, #151520 0%, #1a1a28 50%, #151520 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(136, 204, 255, 0.2), rgba(200, 80, 255, 0.15))',
        'banner_border_color': '#88ccff',
        'category': 'vibrant'
    },
    'solarFlare': {
        'name': 'Solar Flare',
        'description': 'Stellar eruption',
        'accent_color': '#ff6622',
        'background': '''
            radial-gradient(ellipse at 50% 40%, rgba(255, 102, 34, 0.4) 0%, rgba(255, 200, 0, 0.15) 30%, transparent 55%),
            radial-gradient(ellipse at 50% 40%, rgba(255, 50, 0, 0.2) 0%, transparent 40%),
            linear-gradient(180deg, #1a0a05 0%, #120808 50%, #0a0505 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 102, 34, 0.3), rgba(255, 200, 0, 0.15))',
        'banner_border_color': '#ff6622',
        'category': 'vibrant'
    },
    'tropicalPunch': {
        'name': 'Tropical Punch',
        'description': 'Island vibes only',
        'accent_color': '#ff6b9d',
        'background': '''
            radial-gradient(ellipse at 30% 30%, rgba(255, 107, 157, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 70%, rgba(0, 200, 180, 0.25) 0%, transparent 40%),
            radial-gradient(ellipse at 50% 50%, rgba(255, 200, 50, 0.1) 0%, transparent 50%),
            linear-gradient(135deg, #1a0f14 0%, #0f1a1a 50%, #1a0f14 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 107, 157, 0.25), rgba(0, 200, 180, 0.15))',
        'banner_border_color': '#ff6b9d',
        'category': 'vibrant'
    },

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # NATURE & ELEMENTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    'arcticFrost': {
        'name': 'Arctic Frost',
        'description': 'Frozen tundra chill',
        'accent_color': '#a0d8ef',
        'background': '''
            radial-gradient(ellipse at 50% 30%, rgba(160, 216, 239, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(180, 220, 240, 0.15) 0%, transparent 45%),
            linear-gradient(180deg, #0e1418 0%, #121a20 50%, #0c1215 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(160, 216, 239, 0.2), rgba(160, 216, 239, 0.05))',
        'banner_border_color': '#a0d8ef',
        'category': 'nature'
    },
    'desertDusk': {
        'name': 'Desert Dusk',
        'description': 'Sandy twilight horizon',
        'accent_color': '#d4956a',
        'background': '''
            radial-gradient(ellipse at 50% 80%, rgba(212, 149, 106, 0.3) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 30%, rgba(180, 100, 60, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #1a1510 0%, #201a14 50%, #14100c 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(212, 149, 106, 0.25), rgba(212, 149, 106, 0.08))',
        'banner_border_color': '#d4956a',
        'category': 'nature'
    },
    'deepOcean': {
        'name': 'Deep Ocean',
        'description': 'Crushing pressure blue',
        'accent_color': '#1e6091',
        'background': '''
            radial-gradient(ellipse at 50% 100%, rgba(30, 96, 145, 0.3) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 40%, rgba(20, 60, 100, 0.2) 0%, transparent 45%),
            linear-gradient(180deg, #060d14 0%, #0a1520 60%, #050a10 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(30, 96, 145, 0.2), rgba(30, 96, 145, 0.05))',
        'banner_border_color': '#1e6091',
        'category': 'nature'
    },
    'forestCanopy': {
        'name': 'Forest Canopy',
        'description': 'Dappled woodland light',
        'accent_color': '#3a8a4a',
        'background': '''
            radial-gradient(ellipse at 40% 30%, rgba(58, 138, 74, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 60%, rgba(30, 100, 40, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 20% 80%, rgba(50, 120, 60, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #0a120c 0%, #0e1a10 50%, #080f0a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(58, 138, 74, 0.2), rgba(58, 138, 74, 0.05))',
        'banner_border_color': '#3a8a4a',
        'category': 'nature'
    },
    'lavenderFields': {
        'name': 'Lavender Fields',
        'description': 'Rolling purple hills',
        'accent_color': '#9b7ec8',
        'background': '''
            radial-gradient(ellipse at 50% 70%, rgba(155, 126, 200, 0.25) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 30%, rgba(130, 100, 180, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #12101a 0%, #18141f 50%, #0f0d15 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(155, 126, 200, 0.2), rgba(155, 126, 200, 0.05))',
        'banner_border_color': '#9b7ec8',
        'category': 'nature'
    },
    'moltenCore': {
        'name': 'Molten Core',
        'description': 'Volcanic magma flows',
        'accent_color': '#ff4400',
        'background': '''
            radial-gradient(ellipse at 50% 80%, rgba(255, 68, 0, 0.35) 0%, transparent 45%),
            radial-gradient(ellipse at 30% 50%, rgba(255, 140, 0, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 30%, rgba(200, 50, 0, 0.15) 0%, transparent 35%),
            linear-gradient(180deg, #1a0a05 0%, #200e08 50%, #120805 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 68, 0, 0.3), rgba(255, 140, 0, 0.15))',
        'banner_border_color': '#ff4400',
        'category': 'nature'
    },
    'stormFront': {
        'name': 'Storm Front',
        'description': 'Thunderclouds rolling in',
        'accent_color': '#6a7a8a',
        'background': '''
            radial-gradient(ellipse at 50% 20%, rgba(106, 122, 138, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(80, 95, 110, 0.15) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 50%, rgba(90, 100, 120, 0.1) 0%, transparent 45%),
            linear-gradient(180deg, #0e1015 0%, #14181e 50%, #0a0d12 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(106, 122, 138, 0.2), rgba(106, 122, 138, 0.05))',
        'banner_border_color': '#6a7a8a',
        'category': 'nature'
    },
    'coralReef': {
        'name': 'Coral Reef',
        'description': 'Underwater paradise',
        'accent_color': '#ff7f50',
        'background': '''
            radial-gradient(ellipse at 40% 60%, rgba(255, 127, 80, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 30%, rgba(0, 150, 136, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 20% 80%, rgba(255, 200, 100, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #0a1015 0%, #10181a 50%, #0a1015 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 127, 80, 0.25), rgba(0, 150, 136, 0.15))',
        'banner_border_color': '#ff7f50',
        'category': 'nature'
    },

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # METALLIC & PREMIUM
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    'brushedTitanium': {
        'name': 'Brushed Titanium',
        'description': 'Industrial precision',
        'accent_color': '#8899aa',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(136, 153, 170, 0.15) 0%, transparent 60%),
            repeating-linear-gradient(90deg, transparent, transparent 3px, rgba(136, 153, 170, 0.03) 3px, rgba(136, 153, 170, 0.03) 4px),
            linear-gradient(135deg, #141618 0%, #1c1e22 50%, #141618 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(136, 153, 170, 0.15), rgba(136, 153, 170, 0.05))',
        'banner_border_color': '#8899aa',
        'category': 'metallic'
    },
    'copperPatina': {
        'name': 'Copper Patina',
        'description': 'Aged copper warmth',
        'accent_color': '#b87333',
        'background': '''
            radial-gradient(ellipse at 40% 40%, rgba(184, 115, 51, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 60% 70%, rgba(100, 160, 140, 0.15) 0%, transparent 45%),
            linear-gradient(135deg, #151210 0%, #1a1614 50%, #121010 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(184, 115, 51, 0.25), rgba(184, 115, 51, 0.08))',
        'banner_border_color': '#b87333',
        'category': 'metallic'
    },
    'darkChrome': {
        'name': 'Dark Chrome',
        'description': 'Mirror-polished shadows',
        'accent_color': '#aabbcc',
        'background': '''
            radial-gradient(ellipse at 50% 30%, rgba(170, 187, 204, 0.12) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(150, 165, 180, 0.08) 0%, transparent 40%),
            linear-gradient(135deg, #101214 0%, #1a1c20 40%, #101214 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(170, 187, 204, 0.15), rgba(170, 187, 204, 0.05))',
        'banner_border_color': '#aabbcc',
        'category': 'metallic'
    },
    'roseGold': {
        'name': 'Rose Gold',
        'description': 'Elegant pink metal',
        'accent_color': '#e8a0a0',
        'background': '''
            radial-gradient(ellipse at 40% 30%, rgba(232, 160, 160, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 60% 70%, rgba(200, 140, 140, 0.15) 0%, transparent 45%),
            linear-gradient(135deg, #1a1416 0%, #1e1618 50%, #161214 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(232, 160, 160, 0.2), rgba(232, 160, 160, 0.05))',
        'banner_border_color': '#e8a0a0',
        'category': 'metallic'
    },
    'tarnishedGold': {
        'name': 'Tarnished Gold',
        'description': 'Antique golden warmth',
        'accent_color': '#c5a355',
        'background': '''
            radial-gradient(ellipse at 50% 40%, rgba(197, 163, 85, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(160, 130, 60, 0.12) 0%, transparent 40%),
            linear-gradient(135deg, #14120e 0%, #1a1814 50%, #12100c 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(197, 163, 85, 0.2), rgba(197, 163, 85, 0.05))',
        'banner_border_color': '#c5a355',
        'category': 'metallic'
    },
    'blackenedSteel': {
        'name': 'Blackened Steel',
        'description': 'Forged in darkness',
        'accent_color': '#5a6a7a',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(90, 106, 122, 0.15) 0%, transparent 55%),
            repeating-linear-gradient(135deg, transparent, transparent 2px, rgba(90, 106, 122, 0.02) 2px, rgba(90, 106, 122, 0.02) 4px),
            linear-gradient(180deg, #0e1014 0%, #14161a 50%, #0c0e12 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(90, 106, 122, 0.15), rgba(90, 106, 122, 0.05))',
        'banner_border_color': '#5a6a7a',
        'category': 'metallic'
    },

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TROPHY TIERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    'bronzeGlow': {
        'name': 'Bronze Glow',
        'description': 'Every journey starts here',
        'accent_color': '#8d4d2a',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(141, 77, 42, 0.25) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(120, 60, 30, 0.15) 0%, transparent 40%),
            linear-gradient(135deg, #14100c 0%, #1a140f 50%, #100c08 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(141, 77, 42, 0.25), rgba(141, 77, 42, 0.08))',
        'banner_border_color': '#8d4d2a',
        'category': 'trophy'
    },
    'silverFrost': {
        'name': 'Silver Frost',
        'description': 'Cool metallic prestige',
        'accent_color': '#8a8a8a',
        'background': '''
            radial-gradient(ellipse at 50% 40%, rgba(138, 138, 138, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(120, 120, 130, 0.12) 0%, transparent 40%),
            linear-gradient(135deg, #121214 0%, #1a1a1e 50%, #101012 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(138, 138, 138, 0.2), rgba(138, 138, 138, 0.05))',
        'banner_border_color': '#8a8a8a',
        'category': 'trophy'
    },
    'goldRush': {
        'name': 'Gold Rush',
        'description': 'Precious and rare',
        'accent_color': '#d4a843',
        'background': '''
            radial-gradient(ellipse at 50% 40%, rgba(212, 168, 67, 0.25) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 70%, rgba(180, 140, 50, 0.15) 0%, transparent 40%),
            linear-gradient(135deg, #14120c 0%, #1c1810 50%, #12100a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(212, 168, 67, 0.25), rgba(212, 168, 67, 0.08))',
        'banner_border_color': '#d4a843',
        'category': 'trophy'
    },
    'platinumIce': {
        'name': 'Platinum Ice',
        'description': 'The ultimate achievement',
        'accent_color': '#67d1f8',
        'background': '''
            radial-gradient(ellipse at 50% 40%, rgba(103, 209, 248, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(80, 180, 220, 0.12) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 30%, rgba(103, 209, 248, 0.08) 0%, transparent 35%),
            linear-gradient(135deg, #0c1418 0%, #101c22 50%, #0a1216 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(103, 209, 248, 0.25), rgba(103, 209, 248, 0.08))',
        'banner_border_color': '#67d1f8',
        'category': 'trophy'
    },

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RETRO & NOSTALGIA
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    'arcadeCabinet': {
        'name': 'Arcade Cabinet',
        'description': 'Insert coin to continue',
        'accent_color': '#ff4444',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(255, 68, 68, 0.15) 0%, transparent 50%),
            radial-gradient(ellipse at 20% 20%, rgba(0, 100, 255, 0.1) 0%, transparent 30%),
            radial-gradient(ellipse at 80% 80%, rgba(255, 200, 0, 0.1) 0%, transparent 30%),
            linear-gradient(180deg, #0a0a10 0%, #141418 50%, #0a0a0e 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 68, 68, 0.2), rgba(0, 100, 255, 0.1))',
        'banner_border_color': '#ff4444',
        'category': 'retro'
    },
    'crtScanlines': {
        'name': 'CRT Scanlines',
        'description': 'Old school display',
        'accent_color': '#44cc44',
        'background': '''
            repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0, 0, 0, 0.15) 2px, rgba(0, 0, 0, 0.15) 4px),
            radial-gradient(ellipse at 50% 50%, rgba(68, 204, 68, 0.1) 0%, transparent 60%),
            linear-gradient(180deg, #0a100a 0%, #0e150e 50%, #080c08 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(68, 204, 68, 0.15), rgba(68, 204, 68, 0.05))',
        'banner_border_color': '#44cc44',
        'category': 'retro'
    },
    'pixelDust': {
        'name': 'Pixel Dust',
        'description': '8-bit nostalgia',
        'accent_color': '#ff9900',
        'background': '''
            radial-gradient(ellipse at 40% 30%, rgba(255, 153, 0, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 60% 70%, rgba(0, 153, 255, 0.15) 0%, transparent 35%),
            linear-gradient(135deg, #0f0f12 0%, #161620 50%, #0f0f12 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 153, 0, 0.2), rgba(0, 153, 255, 0.15))',
        'banner_border_color': '#ff9900',
        'category': 'retro'
    },
    'vhsTracking': {
        'name': 'VHS Tracking',
        'description': 'Be kind, rewind',
        'accent_color': '#6688cc',
        'background': '''
            repeating-linear-gradient(0deg, transparent, transparent 1px, rgba(100, 130, 200, 0.04) 1px, rgba(100, 130, 200, 0.04) 2px),
            radial-gradient(ellipse at 50% 50%, rgba(102, 136, 204, 0.1) 0%, transparent 60%),
            linear-gradient(180deg, #0c0e14 0%, #10141c 50%, #0a0c12 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(102, 136, 204, 0.15), rgba(102, 136, 204, 0.05))',
        'banner_border_color': '#6688cc',
        'category': 'retro'
    },
    'ps1Memory': {
        'name': 'PS1 Memory',
        'description': 'Do not remove memory card',
        'accent_color': '#8888cc',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(136, 136, 204, 0.15) 0%, transparent 50%),
            radial-gradient(ellipse at 20% 80%, rgba(100, 100, 180, 0.1) 0%, transparent 40%),
            linear-gradient(180deg, #0e0e18 0%, #141420 50%, #0a0a14 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(136, 136, 204, 0.2), rgba(136, 136, 204, 0.05))',
        'banner_border_color': '#8888cc',
        'category': 'retro'
    },

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SEASONAL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    'winterSolstice': {
        'name': 'Winter Solstice',
        'description': 'Longest night of the year',
        'accent_color': '#b0d4f1',
        'background': '''
            radial-gradient(ellipse at 50% 30%, rgba(176, 212, 241, 0.15) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 80%, rgba(200, 220, 240, 0.1) 0%, transparent 40%),
            linear-gradient(180deg, #0c1018 0%, #101820 50%, #0a0e15 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(176, 212, 241, 0.2), rgba(176, 212, 241, 0.05))',
        'banner_border_color': '#b0d4f1',
        'category': 'seasonal'
    },
    'springBloom': {
        'name': 'Spring Bloom',
        'description': 'New growth emerging',
        'accent_color': '#7ec87e',
        'background': '''
            radial-gradient(ellipse at 40% 40%, rgba(126, 200, 126, 0.2) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 70%, rgba(255, 182, 193, 0.15) 0%, transparent 40%),
            radial-gradient(ellipse at 30% 80%, rgba(180, 220, 100, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #0c120e 0%, #101a14 50%, #0a100c 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(126, 200, 126, 0.2), rgba(255, 182, 193, 0.1))',
        'banner_border_color': '#7ec87e',
        'category': 'seasonal'
    },
    'summerHaze': {
        'name': 'Summer Haze',
        'description': 'Hot lazy afternoons',
        'accent_color': '#f0a050',
        'background': '''
            radial-gradient(ellipse at 50% 30%, rgba(240, 160, 80, 0.25) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(255, 200, 100, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #1a1510 0%, #201a12 50%, #151008 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(240, 160, 80, 0.25), rgba(240, 160, 80, 0.08))',
        'banner_border_color': '#f0a050',
        'category': 'seasonal'
    },
    'autumnEmber': {
        'name': 'Autumn Ember',
        'description': 'Falling leaves and bonfires',
        'accent_color': '#cc6633',
        'background': '''
            radial-gradient(ellipse at 40% 40%, rgba(204, 102, 51, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 70%, rgba(180, 60, 20, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 30% 80%, rgba(200, 150, 50, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #14100a 0%, #1a140e 50%, #100c08 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(204, 102, 51, 0.25), rgba(180, 60, 20, 0.1))',
        'banner_border_color': '#cc6633',
        'category': 'seasonal'
    },
    'spookyNight': {
        'name': 'Spooky Night',
        'description': 'Trick or treat!',
        'accent_color': '#ff6600',
        'background': '''
            radial-gradient(ellipse at 50% 60%, rgba(255, 102, 0, 0.35) 0%, rgba(255, 140, 0, 0.1) 35%, transparent 55%),
            radial-gradient(ellipse at 15% 25%, rgba(120, 0, 180, 0.25) 0%, transparent 35%),
            radial-gradient(ellipse at 85% 20%, rgba(80, 0, 130, 0.2) 0%, transparent 30%),
            radial-gradient(ellipse at 30% 85%, rgba(100, 0, 160, 0.2) 0%, transparent 30%),
            radial-gradient(ellipse at 70% 80%, rgba(255, 80, 0, 0.15) 0%, transparent 25%),
            linear-gradient(180deg, #08030f 0%, #0d0618 40%, #0a0410 70%, #060210 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 102, 0, 0.35), rgba(120, 0, 180, 0.2))',
        'banner_border_color': '#ff6600',
        'category': 'seasonal'
    },
    'festiveLights': {
        'name': 'Festive Lights',
        'description': 'Holiday cheer',
        'accent_color': '#cc0000',
        'background': '''
            radial-gradient(ellipse at 20% 30%, rgba(204, 0, 0, 0.2) 0%, transparent 30%),
            radial-gradient(ellipse at 50% 60%, rgba(0, 150, 0, 0.15) 0%, transparent 30%),
            radial-gradient(ellipse at 80% 40%, rgba(204, 0, 0, 0.15) 0%, transparent 25%),
            radial-gradient(ellipse at 40% 80%, rgba(0, 150, 0, 0.1) 0%, transparent 25%),
            linear-gradient(180deg, #0a0808 0%, #101010 50%, #080808 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(204, 0, 0, 0.25), rgba(0, 150, 0, 0.15))',
        'banner_border_color': '#cc0000',
        'category': 'seasonal'
    },

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYSTATION INSPIRED
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    'astralPlayground': {
        'name': 'Astral Playground',
        'description': 'Tiny robot big adventure',
        'accent_color': '#4fc3f7',
        'background': '''
            radial-gradient(ellipse at 40% 30%, rgba(79, 195, 247, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 70%, rgba(255, 193, 7, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 20% 80%, rgba(76, 175, 80, 0.15) 0%, transparent 35%),
            linear-gradient(135deg, #0a1520 0%, #101a28 50%, #0a1520 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(79, 195, 247, 0.3), rgba(255, 193, 7, 0.15))',
        'banner_border_color': '#4fc3f7',
        'category': 'playstation'
    },
    'bloodMoon': {
        'name': 'Blood Moon',
        'description': 'The night of the hunt',
        'accent_color': '#8b0000',
        'background': '''
            radial-gradient(circle at 70% 25%, rgba(139, 0, 0, 0.3) 0%, transparent 35%),
            radial-gradient(ellipse at 30% 60%, rgba(80, 0, 0, 0.2) 0%, transparent 45%),
            radial-gradient(ellipse at 60% 80%, rgba(100, 20, 0, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #0a0505 0%, #140808 40%, #0a0404 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(139, 0, 0, 0.3), rgba(80, 0, 0, 0.15))',
        'banner_border_color': '#8b0000',
        'category': 'playstation'
    },
    'clockworkLombax': {
        'name': 'Clockwork Lombax',
        'description': 'Bolts and blasters',
        'accent_color': '#f5a623',
        'background': '''
            radial-gradient(ellipse at 50% 40%, rgba(245, 166, 35, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 30% 70%, rgba(120, 80, 200, 0.25) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 20%, rgba(245, 200, 50, 0.1) 0%, transparent 30%),
            linear-gradient(135deg, #14100a 0%, #1a1418 50%, #100c0a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(245, 166, 35, 0.3), rgba(120, 80, 200, 0.15))',
        'banner_border_color': '#f5a623',
        'category': 'playstation'
    },
    'clutteredAttic': {
        'name': 'Cluttered Attic',
        'description': 'Unraveling yarn adventures',
        'accent_color': '#cc4444',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(204, 68, 68, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 30%, rgba(180, 140, 80, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #14100c 0%, #1a1510 50%, #120e0a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(204, 68, 68, 0.25), rgba(180, 140, 80, 0.1))',
        'banner_border_color': '#cc4444',
        'category': 'playstation'
    },
    'dimensionRift': {
        'name': 'Dimension Rift',
        'description': 'Purple portals between worlds',
        'accent_color': '#b040e0',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(176, 64, 224, 0.35) 0%, rgba(176, 64, 224, 0.1) 30%, transparent 50%),
            radial-gradient(ellipse at 20% 30%, rgba(255, 100, 200, 0.15) 0%, transparent 35%),
            radial-gradient(ellipse at 80% 70%, rgba(100, 50, 180, 0.2) 0%, transparent 40%),
            linear-gradient(135deg, #0a0515 0%, #14081f 50%, #0a0515 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(176, 64, 224, 0.3), rgba(255, 100, 200, 0.15))',
        'banner_border_color': '#b040e0',
        'category': 'playstation'
    },
    'extinctFauna': {
        'name': 'Extinct Fauna',
        'description': 'Roaring through the ages',
        'accent_color': '#4a90d9',
        'background': '''
            radial-gradient(ellipse at 50% 70%, rgba(74, 144, 217, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 30% 30%, rgba(50, 100, 160, 0.15) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 50%, rgba(80, 130, 200, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #0a0f18 0%, #0e1520 50%, #080c14 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(74, 144, 217, 0.25), rgba(74, 144, 217, 0.08))',
        'banner_border_color': '#4a90d9',
        'category': 'playstation'
    },
    'forbiddenWest': {
        'name': 'Forbidden West',
        'description': 'Overgrown machine ruins',
        'accent_color': '#2ecc71',
        'background': '''
            radial-gradient(ellipse at 60% 70%, rgba(46, 204, 113, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 30% 30%, rgba(255, 107, 53, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 20%, rgba(0, 150, 100, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #0a140e 0%, #101a14 50%, #080f0a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(46, 204, 113, 0.25), rgba(255, 107, 53, 0.15))',
        'banner_border_color': '#2ecc71',
        'category': 'playstation'
    },
    'goldenLeaves': {
        'name': 'Golden Leaves',
        'description': 'Honor among the fallen',
        'accent_color': '#d4a843',
        'background': '''
            radial-gradient(ellipse at 60% 30%, rgba(212, 168, 67, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 30% 70%, rgba(180, 50, 30, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 80% 80%, rgba(200, 160, 50, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #12100a 0%, #1a1610 50%, #0e0c08 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(212, 168, 67, 0.3), rgba(180, 50, 30, 0.15))',
        'banner_border_color': '#d4a843',
        'category': 'playstation'
    },
    'gravityShift': {
        'name': 'Gravity Shift',
        'description': 'Falling upward in style',
        'accent_color': '#e84393',
        'background': '''
            radial-gradient(ellipse at 50% 20%, rgba(232, 67, 147, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 50% 80%, rgba(100, 50, 180, 0.25) 0%, transparent 40%),
            linear-gradient(180deg, #1a0818 0%, #0f0a1a 50%, #140815 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(232, 67, 147, 0.3), rgba(100, 50, 180, 0.15))',
        'banner_border_color': '#e84393',
        'category': 'playstation'
    },
    'infectedSpores': {
        'name': 'Infected Spores',
        'description': 'The world fell apart',
        'accent_color': '#6b8e23',
        'background': '''
            radial-gradient(ellipse at 40% 60%, rgba(107, 142, 35, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 70% 30%, rgba(80, 100, 20, 0.15) 0%, transparent 40%),
            radial-gradient(ellipse at 20% 80%, rgba(60, 80, 30, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #0a0e08 0%, #101408 50%, #080a06 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(107, 142, 35, 0.25), rgba(107, 142, 35, 0.08))',
        'banner_border_color': '#6b8e23',
        'category': 'playstation'
    },
    'midgarPlate': {
        'name': 'Midgar Plate',
        'description': 'Mako energy haze',
        'accent_color': '#00cc88',
        'background': '''
            radial-gradient(ellipse at 50% 80%, rgba(0, 204, 136, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 50% 20%, rgba(0, 150, 100, 0.15) 0%, transparent 40%),
            radial-gradient(ellipse at 30% 50%, rgba(0, 100, 80, 0.1) 0%, transparent 35%),
            linear-gradient(180deg, #080e0c 0%, #0c1a14 50%, #06100a 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(0, 204, 136, 0.25), rgba(0, 204, 136, 0.08))',
        'banner_border_color': '#00cc88',
        'category': 'playstation'
    },
    'phantomThief': {
        'name': 'Phantom Thief',
        'description': 'Looking cool, Joker',
        'accent_color': '#ff0000',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(255, 0, 0, 0.2) 0%, transparent 40%),
            radial-gradient(ellipse at 20% 20%, rgba(200, 0, 0, 0.1) 0%, transparent 30%),
            linear-gradient(135deg, #0a0808 0%, #141010 50%, #0a0808 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 0, 0, 0.3), rgba(255, 0, 0, 0.1))',
        'banner_border_color': '#ff0000',
        'category': 'playstation'
    },
    'pitStopRacing': {
        'name': 'Pit Stop',
        'description': 'Checkered flag awaits',
        'accent_color': '#2196f3',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(33, 150, 243, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 80% 30%, rgba(255, 50, 50, 0.1) 0%, transparent 30%),
            linear-gradient(135deg, #0a0e14 0%, #101820 50%, #0a0e14 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(33, 150, 243, 0.25), rgba(255, 50, 50, 0.1))',
        'banner_border_color': '#2196f3',
        'category': 'playstation'
    },
    'plagueTale': {
        'name': 'Plague Swarm',
        'description': 'Rats in the darkness',
        'accent_color': '#8b7355',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(139, 115, 85, 0.15) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 70%, rgba(100, 80, 50, 0.12) 0%, transparent 40%),
            linear-gradient(180deg, #0c0a08 0%, #14100c 50%, #0a0806 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(139, 115, 85, 0.2), rgba(139, 115, 85, 0.05))',
        'banner_border_color': '#8b7355',
        'category': 'playstation'
    },
    'portalGlow': {
        'name': 'Portal Glow',
        'description': 'Through the looking glass',
        'accent_color': '#ff8c00',
        'background': '''
            radial-gradient(circle at 35% 50%, rgba(0, 150, 255, 0.3) 0%, transparent 30%),
            radial-gradient(circle at 65% 50%, rgba(255, 140, 0, 0.3) 0%, transparent 30%),
            linear-gradient(180deg, #0a0a10 0%, #101018 50%, #08080e 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(0, 150, 255, 0.25), rgba(255, 140, 0, 0.2))',
        'banner_border_color': '#ff8c00',
        'category': 'playstation'
    },
    'shadowOfGiants': {
        'name': 'Shadow of Giants',
        'description': 'Colossal and ancient',
        'accent_color': '#7a8a6a',
        'background': '''
            radial-gradient(ellipse at 50% 30%, rgba(122, 138, 106, 0.15) 0%, transparent 50%),
            radial-gradient(ellipse at 50% 80%, rgba(90, 100, 80, 0.1) 0%, transparent 40%),
            linear-gradient(180deg, #0c0e0a 0%, #141610 50%, #0a0c08 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(122, 138, 106, 0.15), rgba(122, 138, 106, 0.05))',
        'banner_border_color': '#7a8a6a',
        'category': 'playstation'
    },
    'soulsBonfire': {
        'name': 'Souls Bonfire',
        'description': 'Rest here, weary traveler',
        'accent_color': '#e8a030',
        'background': '''
            radial-gradient(ellipse at 50% 70%, rgba(232, 160, 48, 0.35) 0%, rgba(200, 100, 20, 0.1) 35%, transparent 55%),
            radial-gradient(ellipse at 50% 70%, rgba(255, 80, 0, 0.1) 0%, transparent 40%),
            linear-gradient(180deg, #0a0808 0%, #100c08 60%, #080605 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(232, 160, 48, 0.3), rgba(200, 100, 20, 0.1))',
        'banner_border_color': '#e8a030',
        'category': 'playstation'
    },
    'spiderVerse': {
        'name': 'Spider Verse',
        'description': 'With great power...',
        'accent_color': '#e23636',
        'background': '''
            radial-gradient(ellipse at 40% 40%, rgba(226, 54, 54, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 60% 60%, rgba(30, 80, 180, 0.25) 0%, transparent 40%),
            linear-gradient(135deg, #0a0510 0%, #140a18 50%, #0a0510 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(226, 54, 54, 0.3), rgba(30, 80, 180, 0.2))',
        'banner_border_color': '#e23636',
        'category': 'playstation'
    },
    'strandedShores': {
        'name': 'Stranded Shores',
        'description': 'Connecting a broken world',
        'accent_color': '#6a8a9a',
        'background': '''
            radial-gradient(ellipse at 50% 80%, rgba(106, 138, 154, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse at 30% 30%, rgba(80, 110, 130, 0.15) 0%, transparent 40%),
            linear-gradient(180deg, #0c1014 0%, #10161a 50%, #0a0e12 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(106, 138, 154, 0.2), rgba(106, 138, 154, 0.05))',
        'banner_border_color': '#6a8a9a',
        'category': 'playstation'
    },
    'strayCat': {
        'name': 'Stray Signal',
        'description': 'Neon alleys and B-12',
        'accent_color': '#ff6a00',
        'background': '''
            radial-gradient(ellipse at 40% 70%, rgba(255, 106, 0, 0.25) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 30%, rgba(0, 180, 200, 0.2) 0%, transparent 35%),
            radial-gradient(ellipse at 20% 40%, rgba(200, 80, 0, 0.1) 0%, transparent 30%),
            linear-gradient(180deg, #0a0c10 0%, #101418 50%, #080a0e 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(255, 106, 0, 0.25), rgba(0, 180, 200, 0.15))',
        'banner_border_color': '#ff6a00',
        'category': 'playstation'
    },
    'tricksterHat': {
        'name': 'Trickster Hat',
        'description': 'A certain medieval rogue',
        'accent_color': '#6b4aa0',
        'background': '''
            radial-gradient(ellipse at 50% 40%, rgba(107, 74, 160, 0.25) 0%, transparent 45%),
            radial-gradient(ellipse at 30% 70%, rgba(80, 50, 130, 0.15) 0%, transparent 40%),
            radial-gradient(ellipse at 70% 20%, rgba(140, 100, 200, 0.1) 0%, transparent 30%),
            linear-gradient(135deg, #0c0a14 0%, #14101e 50%, #0a0812 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(107, 74, 160, 0.25), rgba(107, 74, 160, 0.08))',
        'banner_border_color': '#6b4aa0',
        'category': 'playstation'
    },
    'urbanStealth': {
        'name': 'Urban Stealth',
        'description': 'Sneaking through shadows',
        'accent_color': '#3a5a3a',
        'background': '''
            radial-gradient(ellipse at 50% 50%, rgba(58, 90, 58, 0.15) 0%, transparent 50%),
            radial-gradient(ellipse at 70% 30%, rgba(40, 60, 40, 0.1) 0%, transparent 40%),
            linear-gradient(180deg, #080a08 0%, #0e120e 50%, #060806 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(58, 90, 58, 0.15), rgba(58, 90, 58, 0.05))',
        'banner_border_color': '#3a5a3a',
        'category': 'playstation'
    },
    'wanderersDream': {
        'name': "Wanderer's Dream",
        'description': 'Sand and ancient light',
        'accent_color': '#e8c870',
        'background': '''
            radial-gradient(ellipse at 50% 30%, rgba(232, 200, 112, 0.25) 0%, transparent 50%),
            radial-gradient(ellipse at 50% 80%, rgba(200, 160, 80, 0.15) 0%, transparent 45%),
            linear-gradient(180deg, #14120c 0%, #1a1810 50%, #100e08 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(232, 200, 112, 0.25), rgba(232, 200, 112, 0.08))',
        'banner_border_color': '#e8c870',
        'category': 'playstation'
    },
    'webSlinger': {
        'name': 'Web Slinger',
        'description': 'Your friendly neighborhood...',
        'accent_color': '#cc1111',
        'background': '''
            radial-gradient(ellipse at 50% 40%, rgba(204, 17, 17, 0.3) 0%, transparent 45%),
            radial-gradient(ellipse at 30% 70%, rgba(0, 80, 180, 0.2) 0%, transparent 40%),
            linear-gradient(135deg, #120808 0%, #0a0810 50%, #100608 100%)
        ''',
        'banner_background': 'linear-gradient(to right, rgba(204, 17, 17, 0.3), rgba(0, 80, 180, 0.15))',
        'banner_border_color': '#cc1111',
        'category': 'playstation'
    },
}


# Theme categories for organized display
THEME_CATEGORIES = [
    ('general', 'General'),
    ('dark_moody', 'Dark & Moody'),
    ('vibrant', 'Vibrant & Colorful'),
    ('nature', 'Nature & Elements'),
    ('metallic', 'Metallic & Premium'),
    ('trophy', 'Trophy Tiers'),
    ('retro', 'Retro & Nostalgia'),
    ('seasonal', 'Seasonal'),
    ('playstation', 'PlayStation Inspired'),
    ('special', 'Special'),
]


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


def get_available_themes_for_grid(include_game_art=False, grouped=False):
    """
    Get themes formatted for the color grid template.

    Args:
        include_game_art: If True, includes themes with requires_game_image=True
        grouped: If True, returns themes grouped by category

    Returns:
        If grouped=False: list of (key, theme_data) tuples (flat, sorted)
        If grouped=True: list of (category_key, category_label, themes_list) tuples
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
            'category': data.get('category', 'general'),
        }

        # For game art themes, add extra properties for JS to use
        if requires_game_image:
            theme_entry['is_game_art'] = True
            theme_entry['game_image_source'] = data.get('game_image_source', 'game_image')

        themes.append((key, theme_entry))

    if not grouped:
        return themes

    # Group by category in the order defined by THEME_CATEGORIES
    from collections import OrderedDict
    category_map = OrderedDict()
    for cat_key, cat_label in THEME_CATEGORIES:
        category_map[cat_key] = (cat_label, [])

    for key, theme_data in themes:
        cat = theme_data.get('category', 'general')
        if cat in category_map:
            category_map[cat][1].append((key, theme_data))
        else:
            category_map.setdefault('general', ('General', []))[1].append((key, theme_data))

    # Return only non-empty categories
    return [(cat_key, cat_label, cat_themes)
            for cat_key, (cat_label, cat_themes) in category_map.items()
            if cat_themes]
