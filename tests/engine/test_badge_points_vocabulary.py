"""Badge XP is called **Badge Points** to the reader (2026-08).

Two sealed economies shared one word. Badge XP lives in `ProfileBadgeStanding` / `SeriesBadgeStanding`;
job XP lives in `ProfileJobXP` and rolls up to Pursuer Level, and the badge rebuild states the separation
as a rule: *"Badge XP + leaderboards live inside the box. They never read/write the jobs/contracts
economy."* A hunter can hold very different ranks in each, so one label for both is a correctness problem
in the reader's head. XP also belongs to the gamification system on the merits -- levels, curves and
grants live there; the badge system has a score.

The rename is LABELS ONLY. `total_badge_xp`, `ProfileBadgeStanding.total_xp`, `SeriesBadgeStanding.xp`,
the `lb:xp:*` Redis keys, `xp_service`, context keys and CSS classes are all untouched -- the same call
the Hunters rename made, where every `profile*` URL name stayed because *"churning them risks a
`{% url %}` typo becoming a 500 to change a string nobody outside the codebase sees."*

So these tests guard the two directions that break it: badge surfaces drifting back to "XP", and an
over-eager sweep renaming CAREER XP, which must keep the word.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / 'templates'

# `/design/` is the workshop, not the product -- those pages are design labs and are deliberately exempt.
EXEMPT_DIRS = ('design',)
CAREER_WORDS = ('career', 'job', 'contract', 'pursuer')
# Whole FILES belonging to the other economy. A per-line keyword check is not enough: a line inside
# career.html reading `<span>XP</span>` says nothing about which economy it is in, and the answer is in
# the path. Both filters are kept -- the path catches career surfaces, the line catches a career figure
# rendered on a shared partial.
# Singular stems, matched as substrings of any path PART, so `job_detail.html` and `jobs_browse.html`
# are both caught. The plural 'jobs' missed `job_detail.html` entirely -- which the guard then flagged as
# a badge surface saying XP, and it was right to: an un-exempted file IS unclassified. Adding the surface
# here is the deliberate act of declaring which economy it belongs to.
CAREER_PATHS = ('career', 'contract', 'job', 'pursuer')

# A rendered text node: between > and <, containing no tags or template expressions.
TEXT_NODE = re.compile(r'>[^<>{}]*\bXP\b[^<>]*<')


def _product_templates():
    for path in TEMPLATES.rglob('*.html'):
        parts = path.relative_to(TEMPLATES).parts
        if any(part in EXEMPT_DIRS for part in parts):
            continue
        if any(c in part for part in parts for c in CAREER_PATHS):
            continue
        yield path


def test_no_badge_surface_still_says_xp_to_the_reader():
    """Scanned as rendered TEXT NODES, not raw source: `xp_on_offer`, `bd-req__xp`, `data-countup="{{ xp }}"`
    and `?tab=xp` are all internal and must survive untouched. Only what a reader sees is renamed.
    """
    offenders = []
    for path in _product_templates():
        for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if not TEXT_NODE.search(line):
                continue
            if any(w in line.lower() for w in CAREER_WORDS):
                continue   # Career XP keeps the word
            offenders.append(f'{path.relative_to(ROOT)}:{line_no}')

    assert not offenders, (
        'these badge surfaces still show "XP" to the reader; badge XP is Badge Points:\n  '
        + '\n  '.join(offenders)
    )


def test_the_career_economy_keeps_the_word_xp():
    """The guard above must not become an excuse to rename everything. Career XP is the OTHER economy and
    the one place `XP` is correct -- if a sweep renamed it too, the two would be confusable again in the
    opposite direction, and this is the only test that would notice."""
    career = (TEMPLATES / 'trophies' / 'career.html').read_text(encoding='utf-8')

    # Matched as a RENDERED text node, with the same regex the other direction uses. Searching the whole
    # file for `\bXP\b` passes on comments and attribute values alone, so a sweep could rename every
    # visible Career label and this would still be green -- which is exactly what mutation testing showed
    # the first version of this assertion doing.
    assert TEXT_NODE.search(career), (
        'the Career page no longer shows XP to the reader -- the rename went too far. Career XP is the '
        'other economy and keeps the word.'
    )


def test_the_internal_names_were_not_churned():
    """Labels only. A rename sweep across live Redis keys and model fields is an outage risk for zero
    reader-visible gain, and this records that the restraint was deliberate rather than an oversight."""
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding

    assert hasattr(ProfileBadgeStanding, 'total_xp'), 'the standing field was renamed; it should not be'
    assert hasattr(SeriesBadgeStanding, 'xp'), 'the per-series field was renamed; it should not be'
