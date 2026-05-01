"""Trophy phase tags for roadmap authoring.

Authors classify each trophy guide with one of these phases ("when in the
playthrough should I do this?"). Viewers can sort the published roadmap by
phase, in which case trophies render as grouped sections rather than a flat
list.

Phases are deliberately curated, not author-defined. Cross-game consistency
lets viewers develop muscle memory: a "Grindy" tag means the same thing on
every guide. If author flexibility becomes a real need we can revisit.

Single source of truth for:
  - TrophyGuide.phase choices (model)
  - editor dropdown options (template + JSON script)
  - published-view sort ordering and badge styling
"""

# (key, label, badge_class, emoji)
# Order here = render order in grouped sort: Story first, Endgame last.
# `key` is what gets stored in the DB; keep it stable since changing it
# would orphan existing tagged guides.
TROPHY_PHASES = (
    ('story',       'Story',                 'badge-info',      '📖'),
    ('challenge',   'Challenge',             'badge-error',     '💪'),
    ('combat',      'Combat',                'badge-secondary', '⚔️'),
    ('secret',      'Secret',                'badge-accent',    '🔍'),
    ('playthrough', 'Restricted Playthrough','badge-primary',   '⏳'),
    ('collectible', 'Collectible',           'badge-success',   '📦'),
    ('side',        'Side / Optional',       'badge-ghost',     '🎯'),
    ('grindy',      'Grindy',                'badge-neutral',   '⏱'),
    ('online',      'Online / Co-op',        'badge-warning',   '🌐'),
    ('endgame',     'Endgame / Cleanup',     'badge-warning',   '🏁'),
)

# (key, label) tuples for Django CharField(choices=...)
PHASE_CHOICES = tuple((key, label) for key, label, _, _ in TROPHY_PHASES)

# Order map: phase key -> integer index (lower = earlier in playthrough).
# Used by the published-view JS to sort sections; "" (unphased) sorts last.
PHASE_ORDER = {key: idx for idx, (key, _, _, _) in enumerate(TROPHY_PHASES)}


def phases_for_template():
    """Return TROPHY_PHASES as a list of dicts for template/JSON consumption."""
    return [
        {'key': key, 'label': label, 'badge_class': badge_class, 'emoji': emoji}
        for key, label, badge_class, emoji in TROPHY_PHASES
    ]


def phases_by_key():
    """Return {key: {label, badge_class, emoji}} for fast template lookup."""
    return {
        key: {'label': label, 'badge_class': badge_class, 'emoji': emoji}
        for key, label, badge_class, emoji in TROPHY_PHASES
    }
