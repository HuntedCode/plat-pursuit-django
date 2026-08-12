"""The recap share card's builder and its PNG endpoint.

All three bugs fixed here were UNREACHABLE BY CONSTRUCTION rather than merely wrong, which is why none of
them ever surfaced as a complaint:

- The builder capped platinum covers at 3 while the card's grid holds 8, and the template's "+N more"
  badge compared the rendered list's length against that same limit -- so a six-platinum month showed
  three and the badge could never fire, because the two quantities were never allowed to differ.
- The PNG endpoint accepted any theme key, including the game-art themes the picker deliberately filters
  out. Those expect an image the recap card never supplies.
- It never passed `image_max_size`, so every cover and icon rendered from the 200px default.
"""
import re

import pytest

from api.recap_views import SHARE_CARD_IMAGE_MAX, SHARE_CARD_PLATINUM_SLOTS, RecapShareImageHTMLView
from trophies.models import MonthlyRecap


def _recap(n_plats):
    return MonthlyRecap(
        year=2026, month=3, total_trophies_earned=147, platinums_earned=n_plats,
        platinums_data=[{'game_name': f'Game {i}', 'game_image': '', 'earned_date': 'Mar 4'}
                        for i in range(n_plats)],
    )


class _Profile:
    display_psn_username = 'HuntedCode'
    psn_username = 'HuntedCode'
    avatar_url = ''
    is_plus = False


@pytest.mark.parametrize('total,shown,overflow', [
    (0, 0, 0),
    (3, 3, 0),
    (SHARE_CARD_PLATINUM_SLOTS, SHARE_CARD_PLATINUM_SLOTS, 0),
    (SHARE_CARD_PLATINUM_SLOTS + 4, SHARE_CARD_PLATINUM_SLOTS, 4),
])
def test_the_grid_is_filled_and_the_overflow_is_counted(total, shown, overflow):
    """The builder fills every slot the grid has, and reports what it dropped separately -- the template
    cannot derive the overflow from a list that has already been truncated."""
    ctx = RecapShareImageHTMLView()._build_template_context(_recap(total), _Profile(), 'landscape')
    assert len(ctx['platinums_data']) == shown
    assert ctx['platinums_overflow'] == overflow


def test_more_platinums_than_slots_actually_produces_a_badge():
    """The specific case that was impossible before: the count shown and the count earned must differ."""
    ctx = RecapShareImageHTMLView()._build_template_context(
        _recap(SHARE_CARD_PLATINUM_SLOTS + 1), _Profile(), 'landscape')
    assert ctx['platinums_overflow'] > 0
    assert len(ctx['platinums_data']) < SHARE_CARD_PLATINUM_SLOTS + 1


def test_the_card_renders_the_slots_it_is_given_without_re_slicing():
    """A `|slice` in the template would silently re-cap the list and reintroduce the same mismatch."""
    from pathlib import Path
    tpl = (Path(__file__).resolve().parents[2] /
           'templates' / 'recap' / 'partials' / 'recap_share_card.html').read_text(encoding='utf-8')
    assert 'for plat in platinums_data %}' in tpl, 'the card re-slices a list the builder already capped'
    assert 'platinums_overflow' in tpl, 'the badge is not driven by the builder-reported overflow'


def test_the_image_budget_matches_the_plat_card():
    """200px was the renderer's default and it was never overridden, so every image on the card was an
    upscaled thumbnail."""
    from api.shareable_views import CARD_IMAGE_MAX
    assert SHARE_CARD_IMAGE_MAX == CARD_IMAGE_MAX
    assert SHARE_CARD_IMAGE_MAX > 200


def test_the_png_endpoint_passes_the_budget_and_validates_the_theme():
    """Source-level, because exercising the endpoint would run Playwright."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / 'api' / 'recap_views.py').read_text(encoding='utf-8')
    png = src[src.index('class RecapShareImagePNGView'):]

    assert 'image_max_size=SHARE_CARD_IMAGE_MAX' in png, 'the PNG renders at the 200px default again'
    assert 'GRADIENT_THEMES' in png, 'the theme parameter is unvalidated'
    assert "requires_game_image" in png, (
        'game-art themes are not rejected -- they expect an image the recap card never supplies'
    )


# ── The rebuilt card ──────────────────────────────────────────────────────────


def _rendered(**over):
    """The card's HTML for a given context. Rendered rather than asserted on the builder, because what
    this card gets wrong is COMPOSITION, and composition only exists once it is rendered."""
    from django.template.loader import render_to_string
    from core.services.completion_card_service import TIER_DISPLAY
    counts = {'platinum': 3, 'gold': 14, 'silver': 34, 'bronze': 96}
    ctx = {
        'month_name': 'March', 'year': 2026, 'username': 'HuntedCode', 'avatar_url': '',
        'total_trophies': 147, 'platinums': 3, 'games_total': 9,
        'tier_counts': [(t, c, counts[t]) for t, c in TIER_DISPLAY if counts[t]],
        'platinums_data': [{'game_image': ''}] * 3, 'platinums_overflow': 0,
        'rarest_trophy': {'name': 'Chalice of the Deep', 'earn_rate': 1.4}, 'rarest_trophy_icon': '',
        'stat_items': [{'label': 'Best day', 'value': 'March 18', 'meta': '31 trophies'}],
        # Every real card has a calendar -- a recap cannot exist for a month with no trophies -- so the
        # default context carries one. Without it these render a card no hunter will ever see.
        'calendar_offset': range(2), 'calendar_active_days': 17,
        'calendar_days': [{'day': d, 'size': 14, 'bg': 'rgba(39, 235, 254, 0.52)', 'plat': d == 5}
                          for d in range(1, 32)],
        'cover_w': 116, 'cover_h': 155,
    }
    ctx.update(over)
    return render_to_string('recap/partials/recap_share_card.html', ctx)


def test_the_card_shares_the_plat_cards_identity():
    """A hunter who has seen one should recognise where the other came from before reading a word, so
    the pieces that carry the identity are shared VERBATIM. Compared against the plat card itself rather
    than against copied literals, because a constant duplicated into a test drifts with neither file."""
    from pathlib import Path
    plat = (Path(__file__).resolve().parents[2] / 'templates' / 'shareables' /
            'plat_card.html').read_text(encoding='utf-8')
    card = _rendered()

    shared = [
        # The ground.
        'radial-gradient(120% 90% at 12% 0%, #232a31 0%, #181d23 45%, #05080c 100%)',
        # Both scrim layers.
        'linear-gradient(100deg, rgba(5, 8, 12, 0.74) 0%, rgba(5, 8, 12, 0.52) 52%, rgba(5, 8, 12, 0.20) 100%)',
        'linear-gradient(to top, rgba(5, 8, 12, 0.66) 0%, rgba(5, 8, 12, 0) 44%)',
        # The Frame, and the hairline that separates every zone.
        'inset: 16px;',
        'rgba(64, 72, 83, 0.55)',
        # The page rhythm.
        'padding: 40px 46px;',
        # The brand block.
        'Platinum Pursuit',
        'platpursuit.com',
    ]
    for token in shared:
        assert token in plat, f'{token!r} is not in the plat card -- this test is comparing to nothing'
        assert token in card, f'the recap card no longer shares {token!r} with the plat card'


def test_the_card_uses_only_the_two_embedded_faces():
    """The renderer embeds Bricolage Grotesque and Inter from static/fonts/. A face that is not in that
    directory cannot render at all, so a third family here is silently the fallback."""
    import re
    families = set()
    for decl in re.findall(r"font-family:\s*([^;\"]+)", _rendered()):
        families.update(f.strip().strip("'\"") for f in decl.split(','))
    assert families <= {'Bricolage Grotesque', 'Inter', 'sans-serif'}, (
        f'unembeddable font families on the card: {families - {"Bricolage Grotesque", "Inter", "sans-serif"}}'
    )


def test_no_css_custom_properties_reach_the_card():
    """Playwright renders this via `set_content()` in an about:blank origin -- no stylesheet, so no
    `--pp-*` resolves. A `var()` here is not a fallback, it is a missing value."""
    assert 'var(--' not in _rendered(), 'a custom property will not resolve in the renderer'


def test_a_month_with_no_platinums_shows_its_rarest_find_instead():
    """The proof band is the card's widest element and must not simply vanish for a quiet month."""
    html = _rendered(platinums=0, platinums_data=[], platinums_overflow=0)
    assert 'Rarest find' in html
    assert 'Platinums earned' not in html


def test_zero_figures_are_dropped_rather_than_printed():
    """A row of zeroes is a worse card than a shorter row, and nobody should be talked out of sharing by
    their own card."""
    html = _rendered(platinums=0, games_total=0, platinums_data=[], platinums_overflow=0)
    # Matched on the LABEL markup, not the bare word: "Platinum" also appears in "Platinum Pursuit",
    # which is on every card, so a substring check here passes or fails for the wrong reason.
    assert '>Games</div>' not in html
    assert '>Platinum</div>' not in html and '>Platinums</div>' not in html
    assert '>Trophies</div>' in html, 'the headline figure is always shown'


def test_the_rarest_find_is_shown_exactly_once():
    """It has moved three times across this rebuild -- footer text, mid-row with its icon, and now the
    footer again beside the covers. What must never change is that it appears once: showing it twice was
    the failure mode each rearrangement risked."""
    assert _rendered().count('Rarest find') == 1


def test_the_body_is_two_panels_not_text_on_a_ground():
    """Two passes arranged this content as bare blocks floating on the ground and shuffled them to plug
    the gaps; the holes moved rather than closed and it read as scattered. Real surfaces make the space
    between them a gutter instead of a hole, and they must be EQUAL height or the shorter one reads as
    unfinished."""
    html = _rendered()
    assert html.count('border-radius: 12px') == 2, 'the body is not two panels'
    # Both stretch: the row is align-items: stretch, not flex-start.
    assert 'align-items: stretch' in html


def test_the_proof_band_drops_the_cover_block_for_a_month_with_no_platinums():
    """The other two blocks spread, rather than the band collapsing or printing an empty label."""
    without = _rendered(platinums=0, platinums_data=[], platinums_overflow=0)
    assert 'Platinums earned' not in without
    assert 'Activity' in without, 'the calendar has to hold the band on its own'


def test_the_activity_calendar_is_on_the_card():
    """The element that makes this read as a MONTH rather than a total: a figure says how much, the grid
    says how it happened. It was dropped in the first pass of the rebuild, which is what made the card
    feel barren and left the right of it empty."""
    html = _rendered(calendar_offset=range(2), calendar_active_days=17,
                     calendar_days=[{'day': d, 'size': 14, 'bg': 'rgba(39, 235, 254, 0.52)',
                                     'plat': d == 5} for d in range(1, 32)])
    assert 'Activity' in html
    assert re.search(r'repeat\(7, \d+px\)', html), 'the seven-column grid is gone'
    assert '17 days' in html


def test_the_calendar_offset_can_express_any_weekday():
    """The old card counted leading blanks with a `forloop.counter0` check inside a seven-item loop, which
    cannot express an offset of more than six and silently drew the wrong month for any that needed one.
    The range is built in the view now, so the template just iterates it."""
    import re
    from pathlib import Path
    tpl = (Path(__file__).resolve().parents[2] / 'templates' / 'recap' / 'partials' /
           'recap_share_card.html').read_text(encoding='utf-8')
    # Comments stripped first. The header explains the old bug and QUOTES it, so scanning raw source
    # finds the explanation and reports it as the live code -- the same trap `_code()` exists for in the
    # controller tests.
    live = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', tpl, flags=re.S)
    live = re.sub(r'\{#.*?#\}', '', live, flags=re.S)
    assert 'calendar_offset' in live
    assert 'forloop.counter0' not in live, 'the offset is being counted out in the template again'

    for offset in range(7):
        html = _rendered(calendar_offset=range(offset), calendar_active_days=3,
                         calendar_days=[{'day': d, 'size': 7, 'bg': 'rgba(138, 147, 159, 0.30)',
                                         'plat': False} for d in range(1, 32)])
        # The blank's height tracks the cell size, so match the shape rather than the number.
        blanks = len(re.findall(r'<div style="height: \d+px;"></div>', html))
        assert blanks == offset, f'offset {offset} produced {blanks} leading blanks'


def test_a_platinum_day_is_ringed_rather_than_just_brighter():
    """Level 4 is "busy" and the ring is "you closed something out". They are different facts, and the
    ring is deliberately OFF the ramp's hue -- ringing it in the ramp colour made the marker invisible on
    exactly the days most likely to be level 4."""
    html = _rendered(calendar_offset=range(0), calendar_active_days=1,
                     calendar_days=[{'day': 1, 'size': 20, 'bg': '#27ebfe', 'plat': True}])
    assert '#ff9350' in html, 'the platinum ring is gone'
    assert 'border: 2px solid #ff9350' in html


def test_the_cards_ramp_matches_the_decks():
    """The card ports `.activity-level-*` from recap-deck.css by hand, because `color-mix()` against
    --pp-* tokens resolves to nothing in the renderer's about:blank origin. A port that drifts means the
    calendar a hunter watched in the ceremony and the one on their card are different pictures."""
    from pathlib import Path
    from api.recap_views import CALENDAR_RAMP
    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components' /
           'recap-deck.css').read_text(encoding='utf-8')

    assert len(CALENDAR_RAMP) == 5, 'the deck has five levels, 0-4'
    for level in range(5):
        assert f'.activity-level-{level}' in css, f'level {level} is not in the deck stylesheet'
    # The ramp must CLIMB in size, which is what stops it depending on hue alone.
    sizes = [size for size, _ in CALENDAR_RAMP]
    assert sizes == sorted(sizes) and len(set(sizes)) == 5, f'the ramp does not climb in size: {sizes}'


def test_the_builder_composes_the_months_texture_as_stat_blocks():
    """Best day and badges sat in a thin footer while a 470x150 hole sat mid-card; they fill the hole
    now. Composed in the builder so the template asks "is there anything to show" once, not three times,
    and only what actually happened is included."""
    recap = _recap(3)
    recap.rarest_trophy_data = {'name': 'Chalice of the Deep', 'earn_rate': 1.4}
    recap.most_active_day = {'date': 'March 18', 'trophy_count': 31}
    recap.activity_calendar = {}
    recap.badges_earned_count = 2
    recap.badge_xp_earned = 4100

    ctx = RecapShareImageHTMLView()._build_template_context(recap, _Profile(), 'landscape')
    labels = [i['label'] for i in ctx['stat_items']]

    assert labels == ['Best day', 'Badges']
    assert 'Rarest' not in labels, 'the rarest find leads the row above; this would show it twice'


def test_a_month_with_no_badges_drops_that_block():
    recap = _recap(1)
    recap.rarest_trophy_data = {}
    recap.most_active_day = {'date': 'March 4', 'trophy_count': 5}
    recap.activity_calendar = {}
    recap.badges_earned_count = 0
    recap.badge_xp_earned = 0

    ctx = RecapShareImageHTMLView()._build_template_context(recap, _Profile(), 'landscape')

    assert [i['label'] for i in ctx['stat_items']] == ['Best day']


def test_the_footer_strip_fits_every_cover_it_shows():
    """The covers are a fixed-size row in the footer, so the widest case has to fit beside the rarest find
    and the stat blocks rather than pushing either off the card."""
    html = _rendered(platinums_data=[{'game_image': ''}] * 8, platinums_overflow=4)
    assert html.count('width: 51px; height: 68px') == 8
    assert '+4' in html

    # 8 covers, their gaps and the "+N" against the card's 1108px content width.
    assert 51 * 8 + 9 * 7 + 40 < 1108
