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
        'spine_items': [{'label': 'Best day', 'value': 'March 18', 'meta': '31 trophies'}],
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


def test_the_proof_band_shows_covers_or_the_rarest_find_but_never_both():
    """The band's left slot is one or the other: covers when the month produced any, the rarest find when
    it did not. Both would have the card saying the same thing twice, since the builder routes the rarest
    trophy to the spine whenever the covers took the slot -- see the builder test below."""
    with_covers = _rendered(platinums_data=[{'game_image': ''}] * 3)
    assert 'Rarest find' not in with_covers, 'the band shows the rarest find beside the covers'
    assert 'Platinums earned' in with_covers

    without = _rendered(platinums=0, platinums_data=[], platinums_overflow=0)
    assert 'Rarest find' in without
    assert 'Platinums earned' not in without


def test_the_card_is_landscape_only():
    """It is a fixed 1200x630 composition. `portrait` renders it into a 1080x1350 viewport -- clipped on
    the right, two thirds empty below -- and the endpoint used to accept it."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / 'api' / 'recap_views.py').read_text(encoding='utf-8')
    assert "format_type not in ['landscape', 'portrait']" not in src, (
        'the endpoints accept a format the card cannot produce'
    )
    assert "format_type != 'landscape'" in src


def test_the_activity_calendar_is_on_the_card():
    """The element that makes this read as a MONTH rather than a total: a figure says how much, the grid
    says how it happened. It was dropped in the first pass of the rebuild, which is what made the card
    feel barren and left the right of it empty."""
    html = _rendered(calendar_offset=range(2), calendar_active_days=17,
                     calendar_days=[{'day': d, 'size': 14, 'bg': 'rgba(39, 235, 254, 0.52)',
                                     'plat': d == 5} for d in range(1, 32)])
    assert 'Activity' in html
    assert 'repeat(7, 24px)' in html, 'the seven-column grid is gone'
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
        assert html.count('<div style="height: 24px;"></div>') == offset, (
            f'offset {offset} did not produce {offset} leading blanks'
        )


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


def test_the_builder_routes_the_rarest_find_to_the_spine_when_covers_take_the_band():
    """The one rule the template cannot express, because it depends on what the OTHER slot got."""
    from types import SimpleNamespace

    def spine_for(n_plats):
        recap = _recap(n_plats)
        recap.rarest_trophy_data = {'name': 'Chalice of the Deep', 'earn_rate': 1.4}
        recap.most_active_day = {'date': 'March 18', 'trophy_count': 31}
        recap.activity_calendar = {}
        recap.badges_earned_count = 0
        recap.badge_xp_earned = 0
        ctx = RecapShareImageHTMLView()._build_template_context(recap, _Profile(), 'landscape')
        return [i['label'] for i in ctx['spine_items']]

    assert 'Rarest' in spine_for(3), 'the covers took the band and nothing carried the rarest find'
    assert 'Rarest' not in spine_for(0), 'the band already shows it; the spine repeats it'
