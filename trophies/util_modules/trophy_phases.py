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
  - published-view sort ordering and each phase's tone
"""

# (key, label, tone, emoji)
# `tone` names a house colour: it picks the `.bd-chip--<tone>` pill and the `--pp-<tone>` stripe of a
# grouped section header. The quiet phases share `ghost`, which has a chip modifier and no colour token,
# so their stripes fall back to the muted text colour.
# Order here = render order in grouped sort: Story first, Endgame last.
# `key` is what gets stored in the DB; keep it stable since changing it
# would orphan existing tagged guides.
TROPHY_PHASES = (
    ('story',       'Story',                 'info',      '📖'),
    ('challenge',   'Challenge',             'error',     '💪'),
    ('combat',      'Combat',                'secondary', '⚔️'),
    ('secret',      'Secret',                'accent',    '🔍'),
    ('playthrough', 'Restricted Playthrough','primary',   '⏳'),
    ('collectible', 'Collectible',           'success',   '📦'),
    ('side',        'Side / Optional',       'ghost',     '🎯'),
    ('grindy',      'Grindy',                'ghost',     '⏱'),
    ('online',      'Online / Co-op',        'warning',   '🌐'),
    ('endgame',     'Endgame / Cleanup',     'warning',   '🏁'),
)

# (key, label) tuples for Django CharField(choices=...)
PHASE_CHOICES = tuple((key, label) for key, label, _, _ in TROPHY_PHASES)

# Order map: phase key -> integer index (lower = earlier in playthrough).
# Used by the published-view JS to sort sections; "" (unphased) sorts last.
PHASE_ORDER = {key: idx for idx, (key, _, _, _) in enumerate(TROPHY_PHASES)}


def phases_for_template():
    """Return TROPHY_PHASES as a list of dicts for template/JSON consumption."""
    return [
        {'key': key, 'label': label, 'tone': tone, 'emoji': emoji}
        for key, label, tone, emoji in TROPHY_PHASES
    ]


def phases_by_key():
    """Return {key: {label, tone, emoji}} for fast template lookup."""
    return {
        key: {'label': label, 'tone': tone, 'emoji': emoji}
        for key, label, tone, emoji in TROPHY_PHASES
    }
