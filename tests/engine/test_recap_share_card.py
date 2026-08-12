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


def test_the_rarest_find_is_never_shown_twice():
    """It rides the proof band beside a few covers, and moves to the spine when the covers earn the
    width. Both at once would have the card saying the same thing twice."""
    beside = _rendered(platinums_data=[{'game_image': ''}] * 3)
    assert beside.count('Rarest') == 1

    many = _rendered(platinums_data=[{'game_image': ''}] * 8, platinums_overflow=4,
                     spine_items=[{'label': 'Rarest', 'value': 'Chalice of the Deep',
                                   'meta': '1.4% earn rate'}])
    assert 'Rarest find' not in many, 'the band still shows it when the covers have taken the width'
    assert 'Rarest' in many, 'and the spine did not pick it up'


def test_the_card_is_landscape_only():
    """It is a fixed 1200x630 composition. `portrait` renders it into a 1080x1350 viewport -- clipped on
    the right, two thirds empty below -- and the endpoint used to accept it."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / 'api' / 'recap_views.py').read_text(encoding='utf-8')
    assert "format_type not in ['landscape', 'portrait']" not in src, (
        'the endpoints accept a format the card cannot produce'
    )
    assert "format_type != 'landscape'" in src
