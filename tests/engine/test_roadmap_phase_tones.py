"""Roadmap phase pills wear the house chip, keyed by a `tone` (the review leftovers: SW-01 in
docs/design/reviews/README.md).

The phases used to carry DaisyUI `badge-*` class names out of Python, which the retired-class guard could
only allowlist. They now carry a house tone, and every surface builds `.bd-chip--<tone>` from it.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from trophies.util_modules.trophy_phases import TROPHY_PHASES, phases_by_key, phases_for_template

ROOT = Path(__file__).resolve().parents[2]
CHIPS = re.sub(r'/\*.*?\*/', '', (ROOT / 'static/css/components/chips.css').read_text(encoding='utf-8'), flags=re.S)


def test_every_tone_has_a_house_chip():
    """A tone without a `.bd-chip--<tone>` rule renders as an unstyled grey pill, silently."""
    tones = {tone for _, _, tone, _ in TROPHY_PHASES}
    defined = set(re.findall(r'\.bd-chip--([a-z]+)\s*[{,]', CHIPS))
    assert tones <= defined, f'tones with no house chip: {sorted(tones - defined)}'


def test_the_payloads_carry_a_tone_not_a_class_name():
    """roadmap-detail.js and roadmap_editor.js read these keys from the JSON script."""
    assert all(set(row) == {'key', 'label', 'tone', 'emoji'} for row in phases_for_template())
    assert all(set(row) == {'label', 'tone', 'emoji'} for row in phases_by_key().values())


@pytest.mark.parametrize('key', ['story', 'grindy'])
def test_the_guide_card_renders_the_phase_as_a_house_chip(key):
    meta = phases_by_key()[key]
    guide = SimpleNamespace(trophy_id=7, phase=key, body='', is_missable=False, is_online=False,
                            is_unobtainable=False)
    trophy = SimpleNamespace(trophy_type='gold', trophy_earn_rate=12.5, trophy_name='A trophy',
                             trophy_detail='', trophy_icon_url='')
    html = render_to_string('trophies/partials/roadmap/trophy_guide_card.html', {
        'guide': guide, 'roadmap_trophies': {7: trophy}, 'profile_earned': {},
        'trophy_phases_by_key': phases_by_key(),
    })
    pill = re.search(r'<span class="([^"]*)" title="Phase: ' + re.escape(meta['label']) + '"', html)
    assert pill, 'the phase pill did not render'
    assert pill.group(1).split() == ['bd-chip', f'bd-chip--{meta["tone"]}', 'shrink-0']
