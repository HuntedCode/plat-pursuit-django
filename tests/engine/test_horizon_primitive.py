"""Tally + Horizon primitive extraction.

The primitives are mostly CSS (visual, verified in-browser). The one piece with real logic is
the Horizon cool->warm band mapping (the `horizon_band` filter, mirrored in horizon.js); this
pins its thresholds and that the convenience partials emit the right classes/attributes.
"""
import pytest
from django.template.loader import render_to_string

from core.templatetags.custom_filters import horizon_band


@pytest.mark.parametrize('pct,band', [
    (0, 'cool'), (29, 'cool'), (29.9, 'cool'),
    (30, 'warming'), (50, 'warming'), (64, 'warming'),
    (65, 'warm'), (89, 'warm'),
    (90, 'hot'), (100, 'hot'), (150, 'hot'),
])
def test_horizon_band_thresholds(pct, band):
    assert horizon_band(pct) == band


def test_horizon_band_handles_bad_input():
    assert horizon_band(None) == 'cool'
    assert horizon_band('') == 'cool'
    assert horizon_band('abc') == 'cool'


def test_horizon_partial_band_tone_computes_band_from_progress():
    html = render_to_string('components/horizon.html', {'progress': 70, 'tone': 'band'})
    assert 'pp-horizon__fill' in html
    assert 'data-horizon-band="warm"' in html        # 70% -> warm
    assert '--horizon-progress: 70%' in html
    assert 'role="progressbar"' in html and 'aria-valuenow="70"' in html


def test_horizon_partial_explicit_band_overrides_progress():
    html = render_to_string('components/horizon.html', {'progress': 10, 'tone': 'band', 'band': 'hot'})
    assert 'data-horizon-band="hot"' in html          # explicit band wins over 10% -> cool


def test_horizon_partial_themed_tone_sets_accent_and_omits_band():
    html = render_to_string('components/horizon.html',
                            {'progress': 40, 'tone': 'themed', 'accent': 'var(--disc)'})
    assert 'data-horizon-band' not in html           # themed has no cool->warm band
    assert '--horizon-accent: var(--disc)' in html


def test_tally_partial_renders_size_and_glow():
    html = render_to_string('components/tally.html', {'value': 47, 'size': 'lg', 'glow': True})
    assert 'pp-tally' in html and 'pp-tally--lg' in html and 'pp-tally--glow' in html
    assert '>47<' in html
