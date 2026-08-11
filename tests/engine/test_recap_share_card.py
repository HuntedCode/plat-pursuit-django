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
