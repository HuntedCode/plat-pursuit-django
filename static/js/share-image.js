/**
 * ShareImageManager - Handles platinum share image generation and preview
 * Uses html2canvas for client-side image generation from HTML template
 * Depends on: PlatPursuit.API, PlatPursuit.ToastManager, html2canvas (CDN)
 *
 * Theme definitions can be loaded from server via window.GRADIENT_THEMES
 * (set by the gradient_themes_json template tag) for single source of truth.
 * Falls back to inline definitions if external themes aren't available.
 */
class ShareImageManager {
    constructor(notificationId, metadata) {
        this.notificationId = notificationId;
        this.metadata = metadata || {};
        this.currentFormat = 'landscape';
        this.currentBackground = 'default';
        this.generatedImages = {};

        // Image dimensions
        this.dimensions = {
            landscape: { width: 1200, height: 630 },
            portrait: { width: 1080, height: 1350 }
        };

        // Build background styles from external themes if available
        this.backgroundStyles = this._buildBackgroundStyles();
    }

    /**
     * Build background styles from window.GRADIENT_THEMES or use inline fallback
     */
    _buildBackgroundStyles() {
        // Check if themes are loaded from server
        if (window.GRADIENT_THEMES && Object.keys(window.GRADIENT_THEMES).length > 0) {
            return this._buildFromExternalThemes(window.GRADIENT_THEMES);
        }
        // Fall back to inline definitions
        return this._getInlineBackgroundStyles();
    }

    /**
     * Convert server-provided themes to the format expected by this class
     */
    _buildFromExternalThemes(themes) {
        const styles = {};

        for (const [key, theme] of Object.entries(themes)) {
            styles[key] = {
                name: theme.name,
                description: theme.description,
                accentColor: theme.accentColor,
                getStyle: theme.requiresGameImage
                    ? function(gameImage) {
                        if (gameImage) {
                            return {
                                background: `linear-gradient(rgba(26, 27, 31, 0.85), rgba(26, 27, 31, 0.9)), url("${gameImage}")`,
                                backgroundSize: 'cover',
                                backgroundPosition: 'center'
                            };
                        }
                        // Fallback to default if no game image
                        return styles.default ? styles.default.getStyle() : { background: theme.background };
                    }
                    : function() {
                        const result = {
                            background: theme.background,
                            backgroundSize: theme.backgroundSize || undefined,
                            backgroundPosition: theme.backgroundPosition || undefined,
                            backgroundRepeat: theme.backgroundRepeat || undefined
                        };
                        // Remove undefined properties
                        Object.keys(result).forEach(k => result[k] === undefined && delete result[k]);
                        return result;
                    },
                getBannerStyle: function() {
                    return {
                        background: theme.bannerBackground,
                        borderColor: theme.bannerBorderColor
                    };
                }
            };
        }

        return styles;
    }

    /**
     * Inline background style definitions (fallback if external themes not loaded)
     */
    _getInlineBackgroundStyles() {
        return {
            apocalypticMoss: {
                name: 'Apocalyptic Moss',
                description: 'Post-apocalyptic greens',
                accentColor: '#4a7c59', // Mossy green
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 20% 30%, rgba(74, 124, 89, 0.3) 0%, transparent 50%),
                        radial-gradient(ellipse at 80% 70%, rgba(101, 67, 33, 0.25) 0%, transparent 45%),
                        radial-gradient(ellipse at 50% 80%, rgba(74, 124, 89, 0.2) 0%, transparent 50%),
                        linear-gradient(to bottom, #0f1512 0%, #1a1f1c 50%, #0f1512 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(74, 124, 89, 0.25), rgba(101, 67, 33, 0.15))',
                    borderColor: '#4a7c59'
                })
            },
            carbonFiber: {
                name: 'Carbon Fiber',
                description: 'Sleek modern subtle',
                accentColor: '#8ba3b8', // Steel blue
                getStyle: () => ({
                    background: `
                        repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(0, 0, 0, 0.1) 2px, rgba(0, 0, 0, 0.1) 4px),
                        linear-gradient(135deg, #1a1b1f 0%, #0f1012 50%, #1a1b1f 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(139, 163, 184, 0.15), rgba(139, 163, 184, 0.05))',
                    borderColor: '#8ba3b8'
                })
            },
            cornerSpotlights: {
                name: 'Corner Glow',
                description: 'Glowing corners effect',
                accentColor: '#67d1f8',
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 0% 0%, rgba(103, 209, 248, 0.4) 0%, transparent 40%),
                        radial-gradient(ellipse at 100% 100%, rgba(149, 128, 255, 0.4) 0%, transparent 40%),
                        linear-gradient(135deg, #1a1b1f 0%, #2a2e34 50%, #1a1b1f 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(103, 209, 248, 0.25), rgba(103, 209, 248, 0.1))',
                    borderColor: '#67d1f8'
                })
            },
            cosmicNebula: {
                name: 'Cosmic Nebula',
                description: 'Space galaxy theme',
                accentColor: '#ff6bdb', // Magenta
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 25% 25%, rgba(149, 128, 255, 0.35) 0%, transparent 45%),
                        radial-gradient(ellipse at 75% 35%, rgba(255, 107, 219, 0.3) 0%, transparent 50%),
                        radial-gradient(ellipse at 50% 75%, rgba(103, 209, 248, 0.25) 0%, transparent 55%),
                        radial-gradient(ellipse at 15% 80%, rgba(149, 128, 255, 0.2) 0%, transparent 40%),
                        radial-gradient(ellipse at 85% 85%, rgba(255, 107, 219, 0.2) 0%, transparent 45%),
                        linear-gradient(to bottom, #0a0515 0%, #1a1b2e 50%, #0a0515 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(255, 107, 219, 0.25), rgba(149, 128, 255, 0.15))',
                    borderColor: '#ff6bdb'
                })
            },
            default: {
                name: 'Default',
                description: 'Subtle diagonal gradient',
                accentColor: '#67d1f8', // Cyan (default platinum color)
                getStyle: () => ({
                    background: 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)'
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(103, 209, 248, 0.15), rgba(103, 209, 248, 0.05))',
                    borderColor: '#67d1f8'
                })
            },
            dragonFlame: {
                name: 'Dragon Flame',
                description: 'Purple dragon fire',
                accentColor: '#a855f7', // Vibrant purple
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 60% 30%, rgba(168, 85, 247, 0.4) 0%, transparent 50%),
                        radial-gradient(ellipse at 20% 70%, rgba(255, 170, 92, 0.35) 0%, transparent 45%),
                        radial-gradient(ellipse at 80% 80%, rgba(168, 85, 247, 0.25) 0%, transparent 50%),
                        linear-gradient(to bottom, #1a0d2e 0%, #0f0618 50%, #1a0d2e 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(168, 85, 247, 0.3), rgba(255, 170, 92, 0.2))',
                    borderColor: '#a855f7'
                })
            },
            electricStorm: {
                name: 'Electric Storm',
                description: 'High energy dramatic',
                accentColor: '#67d1f8', // Bright cyan
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 15% 20%, rgba(103, 209, 248, 0.4) 0%, transparent 30%),
                        radial-gradient(ellipse at 85% 30%, rgba(149, 128, 255, 0.35) 0%, transparent 35%),
                        radial-gradient(ellipse at 50% 80%, rgba(103, 209, 248, 0.3) 0%, transparent 40%),
                        radial-gradient(ellipse at 30% 60%, rgba(149, 128, 255, 0.2) 0%, transparent 30%),
                        linear-gradient(to bottom, #0f0f1a 0%, #1a1b1f 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(103, 209, 248, 0.3), rgba(149, 128, 255, 0.2))',
                    borderColor: '#67d1f8'
                })
            },
            emeraldMatrix: {
                name: 'Emerald Matrix',
                description: 'Digital tech aesthetic',
                accentColor: '#67f8c8', // Success green
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 10% 30%, rgba(103, 248, 200, 0.25) 0%, transparent 50%),
                        radial-gradient(ellipse at 90% 70%, rgba(103, 248, 200, 0.15) 0%, transparent 50%),
                        linear-gradient(180deg, #0a1a14 0%, #050f0a 50%, #0a1a14 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(103, 248, 200, 0.25), rgba(103, 248, 200, 0.08))',
                    borderColor: '#67f8c8'
                })
            },
            gameArtBlur: {
                name: 'Game Art',
                description: 'Blurred game cover background',
                accentColor: '#67d1f8',
                getStyle: (gameImage) => {
                    if (gameImage) {
                        return {
                            background: `linear-gradient(rgba(26, 27, 31, 0.85), rgba(26, 27, 31, 0.9)), url("${gameImage}")`,
                            backgroundSize: 'cover',
                            backgroundPosition: 'center'
                        };
                    }
                    // Fallback if no game image
                    return this.backgroundStyles.default.getStyle();
                },
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(103, 209, 248, 0.2), rgba(103, 209, 248, 0.08))',
                    borderColor: '#67d1f8'
                })
            },
            gradientMesh: {
                name: 'Gradient Mesh',
                description: 'Colorful overlapping gradients',
                accentColor: '#67d1f8',
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 20% 20%, rgba(103, 209, 248, 0.3) 0%, transparent 50%),
                        radial-gradient(ellipse at 80% 80%, rgba(149, 128, 255, 0.3) 0%, transparent 50%),
                        radial-gradient(ellipse at 80% 20%, rgba(255, 170, 92, 0.15) 0%, transparent 40%),
                        linear-gradient(to bottom right, #2a2e34, #1a1b1f, #2a2e34)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(103, 209, 248, 0.2), rgba(149, 128, 255, 0.15))',
                    borderColor: '#67d1f8'
                })
            },
            inferiority: {
                name: 'Inferiority',
                description: 'For Xbox sympathizers',
                accentColor: '#107c10', // Xbox green
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 50% 50%, rgba(16, 124, 16, 0.2) 0%, transparent 60%),
                        linear-gradient(135deg, #0a0f0a 0%, #1a1f1a 50%, #0a0f0a 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(16, 124, 16, 0.15), rgba(16, 124, 16, 0.05))',
                    borderColor: '#107c10'
                })
            },
            jungleMarsupial: {
                name: 'Jungle Marsupial',
                description: 'Orange jungle energy',
                accentColor: '#ff8c00', // Dark orange
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 40% 40%, rgba(255, 140, 0, 0.35) 0%, transparent 50%),
                        radial-gradient(ellipse at 70% 20%, rgba(34, 139, 34, 0.25) 0%, transparent 45%),
                        radial-gradient(ellipse at 20% 80%, rgba(255, 140, 0, 0.25) 0%, transparent 40%),
                        linear-gradient(135deg, #1a1510 0%, #0f0f0a 50%, #1a1510 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(255, 140, 0, 0.3), rgba(34, 139, 34, 0.2))',
                    borderColor: '#ff8c00'
                })
            },
            logoBackdrop: {
                name: 'Logo Backdrop',
                description: 'PlatPursuit branding',
                accentColor: '#67d1f8', // Cyan (brand color)
                getStyle: () => {
                    // Use the static logo path with repeating pattern
                    const logoUrl = '/static/images/logo.png';
                    return {
                        background: `
                            linear-gradient(rgba(26, 27, 31, 0.88), rgba(26, 27, 31, 0.88)),
                            url("${logoUrl}")
                        `.replace(/\s+/g, ' ').trim(),
                        backgroundSize: 'auto, 120px',
                        backgroundPosition: 'center, center',
                        backgroundRepeat: 'repeat, repeat'
                    };
                },
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(103, 209, 248, 0.2), rgba(103, 209, 248, 0.08))',
                    borderColor: '#67d1f8'
                })
            },
            machineHunter: {
                name: 'Machine Hunter',
                description: 'Tribal tech sunrise',
                accentColor: '#ff6b35', // Vibrant orange-red
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 50% 100%, rgba(255, 107, 53, 0.4) 0%, rgba(255, 140, 0, 0.25) 35%, transparent 65%),
                        radial-gradient(ellipse at 30% 30%, rgba(0, 191, 255, 0.2) 0%, transparent 40%),
                        linear-gradient(0deg, #2a1810 0%, #1a1520 50%, #0a0f1a 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(255, 107, 53, 0.3), rgba(0, 191, 255, 0.15))',
                    borderColor: '#ff6b35'
                })
            },
            midnightCrimson: {
                name: 'Midnight Crimson',
                description: 'Bold and rare',
                accentColor: '#ff5757', // Crimson red
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 20% 20%, rgba(255, 87, 87, 0.3) 0%, transparent 50%),
                        radial-gradient(ellipse at 80% 80%, rgba(200, 50, 50, 0.25) 0%, transparent 50%),
                        linear-gradient(135deg, #1a0f0f 0%, #2a1a1a 50%, #1a0f0f 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(255, 87, 87, 0.25), rgba(255, 87, 87, 0.08))',
                    borderColor: '#ff5757'
                })
            },
            minimalDark: {
                name: 'Minimal Dark',
                description: 'Clean dark with vignette',
                accentColor: '#67d1f8',
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at center, #2a2e34 0%, #1a1b1f 70%, #0f1012 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(103, 209, 248, 0.12), rgba(103, 209, 248, 0.04))',
                    borderColor: '#67d1f8'
                })
            },
            neonPurple: {
                name: 'Neon Purple',
                description: 'Vibrant purple atmosphere',
                accentColor: '#9580ff', // Purple accent
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 30% 0%, rgba(149, 128, 255, 0.4) 0%, transparent 50%),
                        radial-gradient(ellipse at 70% 100%, rgba(103, 209, 248, 0.2) 0%, transparent 50%),
                        linear-gradient(180deg, #1a1b2e 0%, #0f0f1a 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(149, 128, 255, 0.25), rgba(149, 128, 255, 0.08))',
                    borderColor: '#9580ff'
                })
            },
            platinumShimmer: {
                name: 'Platinum Shimmer',
                description: 'Metallic prestigious',
                accentColor: '#e8e8e8', // Light platinum
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 50% 30%, rgba(232, 232, 232, 0.15) 0%, transparent 60%),
                        radial-gradient(ellipse at 30% 70%, rgba(200, 200, 200, 0.12) 0%, transparent 50%),
                        radial-gradient(ellipse at 70% 60%, rgba(180, 180, 180, 0.1) 0%, transparent 45%),
                        linear-gradient(135deg, #1a1b1f 0%, #2a2e34 50%, #1a1b1f 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(232, 232, 232, 0.2), rgba(232, 232, 232, 0.08))',
                    borderColor: '#e8e8e8'
                })
            },
            playstationBlue: {
                name: 'PlayStation Blue',
                description: 'Classic blue sweep',
                accentColor: '#4a9eff', // Brighter blue for PS theme
                getStyle: () => ({
                    background: `
                        linear-gradient(135deg, #0d1b2a 0%, #1b3a5f 30%, #0d1b2a 60%, #1a1b1f 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(74, 158, 255, 0.25), rgba(74, 158, 255, 0.08))',
                    borderColor: '#4a9eff'
                })
            },
            rainbowRoad: {
                name: 'Rainbow Road',
                description: 'Wait wrong console...',
                accentColor: '#ff6bdb', // Magenta
                getStyle: () => ({
                    background: `
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
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(255, 0, 255, 0.25), rgba(0, 127, 255, 0.2))',
                    borderColor: '#ff6bdb'
                })
            },
            retroWave: {
                name: 'Retro Wave',
                description: 'Synthwave vaporwave',
                accentColor: '#ff6bdb', // Hot pink
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 50% 120%, rgba(255, 20, 147, 0.5) 0%, rgba(138, 43, 226, 0.3) 40%, transparent 70%),
                        radial-gradient(ellipse at 50% -20%, rgba(0, 191, 255, 0.4) 0%, rgba(138, 43, 226, 0.2) 40%, transparent 70%),
                        repeating-linear-gradient(0deg, transparent, transparent 40px, rgba(255, 20, 147, 0.03) 40px, rgba(255, 20, 147, 0.03) 42px),
                        linear-gradient(180deg, #0d0221 0%, #1a0933 30%, #2d1b4e 50%, #1a0933 70%, #0d0221 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(255, 20, 147, 0.35), rgba(0, 191, 255, 0.25))',
                    borderColor: '#ff14c8'
                })
            },
            sunsetGold: {
                name: 'Sunset Gold',
                description: 'Warm golden tones',
                accentColor: '#ffaa5c', // Gold/orange accent
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 70% 20%, rgba(255, 170, 92, 0.35) 0%, transparent 50%),
                        radial-gradient(ellipse at 20% 80%, rgba(255, 136, 51, 0.2) 0%, transparent 50%),
                        linear-gradient(to bottom, #2a2520 0%, #1a1815 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(255, 170, 92, 0.25), rgba(255, 170, 92, 0.08))',
                    borderColor: '#ffaa5c'
                })
            },
            treasureMap: {
                name: 'Treasure Map',
                description: 'Parchment adventure',
                accentColor: '#d4a574', // Tan/beige
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 40% 40%, rgba(212, 165, 116, 0.2) 0%, transparent 60%),
                        radial-gradient(ellipse at 70% 70%, rgba(139, 90, 43, 0.15) 0%, transparent 50%),
                        linear-gradient(135deg, #1a1510 0%, #2a2015 50%, #1a1510 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(212, 165, 116, 0.25), rgba(139, 90, 43, 0.15))',
                    borderColor: '#d4a574'
                })
            },
            trophySpotlight: {
                name: 'Trophy Spotlight',
                description: 'Center focus effect',
                accentColor: '#ffe66d', // Bright yellow
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at center, rgba(255, 230, 109, 0.25) 0%, rgba(255, 230, 109, 0.1) 30%, transparent 70%),
                        radial-gradient(ellipse at center, #2a2e34 0%, #1a1b1f 60%, #0f1012 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(255, 230, 109, 0.2), rgba(255, 230, 109, 0.08))',
                    borderColor: '#ffe66d'
                })
            },
            warriorRage: {
                name: 'Warrior Rage',
                description: 'Spartan battle fury',
                accentColor: '#c41e3a', // Deep red
                getStyle: () => ({
                    background: `
                        radial-gradient(ellipse at 30% 20%, rgba(196, 30, 58, 0.4) 0%, transparent 50%),
                        radial-gradient(ellipse at 70% 80%, rgba(139, 0, 0, 0.35) 0%, transparent 45%),
                        radial-gradient(ellipse at 50% 50%, rgba(255, 69, 0, 0.15) 0%, transparent 60%),
                        linear-gradient(135deg, #1a0a0a 0%, #2a1010 40%, #1a0505 100%)
                    `.replace(/\s+/g, ' ').trim()
                }),
                getBannerStyle: () => ({
                    background: 'linear-gradient(to right, rgba(196, 30, 58, 0.35), rgba(139, 0, 0, 0.2))',
                    borderColor: '#c41e3a'
                })
            }
        };
    }

    /**
     * Render the share section UI
     */
    renderShareSection() {
        return `
            <div class="mt-6 border-t border-base-300 pt-6" id="share-image-section">
                <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
                    </svg>
                    Share Your Achievement
                </h3>

                <!-- Format & Background Selectors -->
                <div class="flex flex-wrap gap-4 mb-4 items-center">
                    <div class="flex gap-2">
                        <button class="btn btn-sm format-btn btn-primary" data-format="landscape">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                            </svg>
                            Landscape
                        </button>
                        <button class="btn btn-sm format-btn btn-outline" data-format="portrait">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
                            </svg>
                            Portrait
                        </button>
                    </div>

                    <div class="flex items-center gap-2">
                        <label for="background-select" class="text-sm text-base-content/70">Background:</label>
                        <select id="background-select" class="select select-sm select-bordered bg-base-200">
                            ${this.renderBackgroundOptions()}
                        </select>
                    </div>
                </div>

                <!-- Preview Container -->
                <div class="relative bg-base-300 rounded-lg overflow-hidden mb-4">
                    <div id="share-preview-container" class="w-full overflow-x-auto py-4 px-2" style="max-height: 450px;">
                        <div id="share-preview" class="mx-auto shadow-xl rounded-lg overflow-hidden" style="transform-origin: top center;">
                            <!-- Preview rendered here -->
                        </div>
                    </div>
                    <div id="preview-loading" class="absolute inset-0 flex items-center justify-center bg-base-300/80">
                        <span class="loading loading-spinner loading-lg text-primary"></span>
                    </div>
                </div>

                <!-- Format info -->
                <p class="text-xs text-base-content/60 mb-4" id="format-info">
                    Landscape (1200x630)
                </p>

                <!-- Actions -->
                <div class="flex gap-2 flex-wrap">
                    <button id="generate-image-btn" class="btn btn-primary">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                        Download Image
                    </button>
                    <button id="generate-both-btn" class="btn btn-secondary btn-outline">
                        Download Both Formats
                    </button>
                </div>

                <!-- Hidden container for full-size rendering -->
                <div id="share-render-container" style="position: absolute; left: -9999px; top: 0;"></div>

                <!-- Error message -->
                <div id="share-error" class="alert alert-error mt-4 hidden">
                    <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span id="share-error-message"></span>
                </div>
            </div>
        `;
    }

    /**
     * Render background dropdown options
     * Default is always first, then alphabetically sorted
     */
    renderBackgroundOptions() {
        const entries = Object.entries(this.backgroundStyles);

        // Separate default from others
        const defaultEntry = entries.find(([key]) => key === 'default');
        const otherEntries = entries.filter(([key]) => key !== 'default');

        // Sort others alphabetically by name
        otherEntries.sort((a, b) => a[1].name.localeCompare(b[1].name));

        // Combine with default first
        const sortedEntries = defaultEntry ? [defaultEntry, ...otherEntries] : otherEntries;

        return sortedEntries
            .map(([key, style]) => `<option value="${key}">${style.name}</option>`)
            .join('');
    }

    /**
     * Apply selected background style to an element and its banner
     * @param {HTMLElement} element - The .share-image-content element to style
     */
    applyBackground(element) {
        if (!element) return;

        const styleKey = this.currentBackground;
        const styleDef = this.backgroundStyles[styleKey];

        if (!styleDef) return;

        // Get game image for styles that use it
        const gameImage = this.metadata.game_image || null;
        const styles = styleDef.getStyle(gameImage);

        // Apply background styles to main element
        Object.entries(styles).forEach(([prop, value]) => {
            element.style[prop] = value;
        });

        // Apply banner styles if getBannerStyle exists
        if (styleDef.getBannerStyle) {
            const banner = element.querySelector('[data-element="platinum-banner"]');
            if (banner) {
                const bannerStyles = styleDef.getBannerStyle();

                // Apply background
                if (bannerStyles.background) {
                    banner.style.background = bannerStyles.background;
                }

                // Apply border color (handles both landscape border-left and portrait border-top/bottom)
                if (bannerStyles.borderColor) {
                    banner.style.borderLeftColor = bannerStyles.borderColor;
                    banner.style.borderTopColor = bannerStyles.borderColor;
                    banner.style.borderBottomColor = bannerStyles.borderColor;
                }
            }
        }
    }

    /**
     * Initialize event listeners
     */
    init() {
        // Format buttons
        document.querySelectorAll('.format-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.format-btn').forEach(b => {
                    b.classList.remove('btn-primary');
                    b.classList.add('btn-outline');
                });
                e.currentTarget.classList.remove('btn-outline');
                e.currentTarget.classList.add('btn-primary');
                this.currentFormat = e.currentTarget.dataset.format;
                this.updateFormatInfo();
                this.renderPreview();
            });
        });

        // Generate button
        document.getElementById('generate-image-btn')?.addEventListener('click', () => {
            this.generateAndDownload(this.currentFormat);
        });

        // Generate both button
        document.getElementById('generate-both-btn')?.addEventListener('click', () => {
            this.generateAndDownload('both');
        });

        // Background selector
        document.getElementById('background-select')?.addEventListener('change', (e) => {
            this.currentBackground = e.target.value;
            this.renderPreview();
        });

        // Render initial preview
        this.renderPreview();
    }

    /**
     * Update format info text
     */
    updateFormatInfo() {
        const infoEl = document.getElementById('format-info');
        if (!infoEl) return;

        if (this.currentFormat === 'landscape') {
            infoEl.textContent = 'Landscape (1200x630)';
        } else {
            infoEl.textContent = 'Portrait (1080x1350)';
        }
    }

    /**
     * Fetch HTML template from server and render preview
     */
    async renderPreview() {
        const container = document.getElementById('share-preview');
        const loading = document.getElementById('preview-loading');

        if (!container) return;

        loading?.classList.remove('hidden');

        try {
            // Fetch rendered HTML from server
            const html = await this.fetchCardHTML(this.currentFormat);

            const { width, height } = this.dimensions[this.currentFormat];

            // Calculate scale to fit in preview container (max 400px height)
            const maxHeight = 380;
            const scale = Math.min(1, maxHeight / height, 500 / width);

            // Insert the HTML
            container.innerHTML = html;
            container.style.width = `${width * scale}px`;
            container.style.height = `${height * scale}px`;

            // Scale the inner content and apply background
            const innerContent = container.querySelector('.share-image-content');
            if (innerContent) {
                innerContent.style.transform = `scale(${scale})`;
                innerContent.style.transformOrigin = 'top left';
                // Apply the selected background style
                this.applyBackground(innerContent);
            }
        } catch (error) {
            console.error('Failed to render preview:', error);
            container.innerHTML = '<div class="p-4 text-error">Failed to load preview</div>';
        } finally {
            loading?.classList.add('hidden');
        }
    }

    /**
     * Fetch the card HTML from the server
     */
    async fetchCardHTML(format) {
        // Note: Using 'image_format' instead of 'format' because DRF reserves 'format' for content negotiation
        const response = await PlatPursuit.API.get(
            `/api/v1/notifications/${this.notificationId}/share-image/html/?image_format=${format}`
        );

        if (response && response.html) {
            return response.html;
        }
        throw new Error('Failed to fetch card HTML');
    }

    /**
     * Generate image using html2canvas and download
     */
    async generateAndDownload(format) {
        const btn = document.getElementById('generate-image-btn');
        const bothBtn = document.getElementById('generate-both-btn');
        const errorEl = document.getElementById('share-error');
        const renderContainer = document.getElementById('share-render-container');

        if (!renderContainer) return;

        try {
            errorEl?.classList.add('hidden');
            btn.classList.add('loading');
            btn.disabled = true;
            bothBtn.disabled = true;

            const formats = format === 'both' ? ['landscape', 'portrait'] : [format];

            for (const fmt of formats) {
                await this.generateSingleImage(fmt, renderContainer);
            }

            PlatPursuit.ToastManager.success('Image downloaded successfully!');
        } catch (error) {
            console.error('Image generation failed:', error);
            this.showError(error.message || 'Failed to generate image. Please try again.');
            PlatPursuit.ToastManager.error('Failed to generate image');
        } finally {
            btn.classList.remove('loading');
            btn.disabled = false;
            bothBtn.disabled = false;
            renderContainer.innerHTML = '';
        }
    }

    /**
     * Convert a single oklch color to rgba using canvas
     */
    oklchToRgba(oklchColor) {
        const canvas = document.createElement('canvas');
        canvas.width = 1;
        canvas.height = 1;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = oklchColor;
        ctx.fillRect(0, 0, 1, 1);
        const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
        return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
    }

    /**
     * Convert oklch colors in a string to rgba
     */
    convertOklchInString(str) {
        // Match oklch(...) including nested parentheses for calc() etc.
        const oklchRegex = /oklch\([^)]+\)/g;
        return str.replace(oklchRegex, (match) => {
            return this.oklchToRgba(match);
        });
    }

    /**
     * Convert oklch colors to rgb for html2canvas compatibility
     * html2canvas doesn't support oklch() color function used by Tailwind CSS v4
     */
    convertOklchToRgb(element) {
        const allElements = [element, ...element.querySelectorAll('*')];

        // Properties that can contain colors
        const colorProperties = [
            'color', 'backgroundColor', 'borderColor',
            'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor',
            'outlineColor', 'textDecorationColor', 'fill', 'stroke'
        ];

        allElements.forEach(el => {
            const computed = window.getComputedStyle(el);

            colorProperties.forEach(prop => {
                const value = computed[prop];
                if (value && value.includes('oklch')) {
                    el.style[prop] = this.convertOklchInString(value);
                }
            });

            // Handle box-shadow which may have multiple oklch colors
            const boxShadow = computed.boxShadow;
            if (boxShadow && boxShadow.includes('oklch')) {
                el.style.boxShadow = this.convertOklchInString(boxShadow);
            }

            // Handle gradient backgrounds which might use oklch
            const bgImage = computed.backgroundImage;
            if (bgImage && bgImage.includes('oklch')) {
                el.style.backgroundImage = this.convertOklchInString(bgImage);
            }
        });
    }

    /**
     * Generate a single image format
     */
    async generateSingleImage(format, renderContainer) {
        const { width, height } = this.dimensions[format];

        // Fetch the full-size HTML
        const html = await this.fetchCardHTML(format);

        // Create an iframe to isolate from global CSS (prevents oklch inheritance)
        const iframe = document.createElement('iframe');
        iframe.style.cssText = `width: ${width}px; height: ${height}px; position: absolute; left: 0; top: 0; border: none; visibility: hidden;`;
        renderContainer.appendChild(iframe);

        // Wait for iframe to be ready
        await new Promise(resolve => {
            iframe.onload = resolve;
            // Trigger load for about:blank
            iframe.src = 'about:blank';
        });

        const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;

        // Write minimal HTML with no external CSS
        iframeDoc.open();
        iframeDoc.write(`
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    * { margin: 0; padding: 0; box-sizing: border-box; }
                    body { margin: 0; padding: 0; }
                    img { display: inline-block; }
                </style>
            </head>
            <body>${html}</body>
            </html>
        `);
        iframeDoc.close();

        const wrapper = iframeDoc.body.firstElementChild;

        // Apply the selected background style to the wrapper
        const shareImageContent = iframeDoc.querySelector('.share-image-content');
        if (shareImageContent) {
            this.applyBackground(shareImageContent);
        }

        // Wait for images to load inside iframe
        await this.waitForImages(iframeDoc.body);

        // Use html2canvas to generate the image (no oklch colors in isolated iframe)
        const canvas = await html2canvas(wrapper, {
            width: width,
            height: height,
            scale: 1,
            useCORS: true,
            allowTaint: false,
            backgroundColor: null,
            logging: true,
            windowWidth: width,
            windowHeight: height,
            foreignObjectRendering: true,
            window: iframe.contentWindow,
        });

        // Convert to blob and download
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));

        // Create safe filename
        const gameName = (this.metadata.game_name || 'platinum')
            .replace(/[^a-z0-9]/gi, '_')
            .substring(0, 30);

        const filename = `platinum_${gameName}_${format}.png`;

        // Download the file
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        // Clean up
        renderContainer.removeChild(iframe);
    }

    /**
     * Wait for all images in container to load and convert to base64
     */
    async waitForImages(container) {
        const images = container.querySelectorAll('img');
        const promises = Array.from(images).map(async (img) => {
            // Wait for image to load first
            if (!img.complete) {
                await new Promise((resolve) => {
                    img.onload = resolve;
                    img.onerror = resolve;
                });
            }

            // Convert to base64 to avoid CORS issues with foreignObjectRendering
            try {
                const dataUrl = await this.imageToBase64(img.src);
                if (dataUrl) {
                    img.src = dataUrl;
                }
            } catch (e) {
                console.warn('Could not convert image to base64:', img.src, e);
            }
        });
        return Promise.all(promises);
    }

    /**
     * Convert an image URL to base64 data URL
     */
    async imageToBase64(url) {
        // Skip if already a data URL
        if (url.startsWith('data:')) {
            return url;
        }

        try {
            const response = await fetch(url, { mode: 'cors' });
            const blob = await response.blob();
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
        } catch (e) {
            console.warn('Failed to fetch image for base64 conversion:', url, e);
            return null;
        }
    }

    /**
     * Show error message
     */
    showError(message) {
        const errorEl = document.getElementById('share-error');
        const errorMsg = document.getElementById('share-error-message');

        if (errorEl && errorMsg) {
            errorMsg.textContent = message;
            errorEl.classList.remove('hidden');
        }
    }
}

// Export for use
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ShareImageManager = ShareImageManager;
