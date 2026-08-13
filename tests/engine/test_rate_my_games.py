"""Rate My Games -- the rating wizard at /community/rate-my-games/.

Rebuilt 2026-08. The page was pre-rebuild DaisyUI AND a second hand-rolled copy of the rating form, which
had already drifted: no quick take, no live slider readouts, no field-level errors. What these pin is the
thing that stops it drifting again -- both rating surfaces include ONE field partial and post through ONE
controller -- plus the queue behaviour the page is built around.

The look and the motion were checked in a browser, since neither is visible from here.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext

from tests.engine.test_plat_cards import _completed_game
from tests.factories import ConceptTrophyGroupFactory, ProfileFactory
from trophies.models import UserConceptRating

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
URL = '/community/rate-my-games/'
QUEUE = '/api/v1/ratings/wizard/queue/'

# The API contract. Renaming any of these silently breaks the POST on BOTH surfaces at once now.
FIELD_NAMES = ('difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking', 'overall_rating', 'blurb')


def _hunter():
    return ProfileFactory(is_linked=True)


def _page(client, profile):
    client.force_login(profile.user)
    resp = client.get(URL)
    assert resp.status_code == 200, f'expected the page, got {resp.status_code} -> {resp.get("Location", "")}'
    return resp


def _ratable(profile, name=None):
    """A finished game the wizard would queue, with the trophy group the rate endpoint needs."""
    game, _, _ = _completed_game(profile, with_platinum=True, name=name)
    ConceptTrophyGroupFactory(concept=game.concept, trophy_group_id='default', display_name='Base Game')
    return game


# ── One form, two hosts ───────────────────────────────────────────────────────────────────────────

def test_both_rating_surfaces_include_the_same_field_partial():
    """The whole point of the refactor. Two copies of these fields is how the wizard ended up without a
    quick take while the modal had one -- so neither host may own its own copy."""
    for tpl in ('trophies/partials/game_detail/quick_rate_modal.html', 'trophies/rate_my_games.html'):
        src = (ROOT / 'templates' / tpl).read_text(encoding='utf-8')
        assert "'partials/_rating_fields.html'" in src, f'{tpl} does not include the shared fields'


def test_the_wizard_renders_every_field_the_api_expects(client):
    profile = _hunter()
    content = _page(client, profile).content.decode()

    for name in FIELD_NAMES:
        assert f'name="{name}"' in content, f'{name} is missing from the wizard form'
    assert 'data-gd-qr-count' in content, 'the quick take has no character counter'
    assert 'data-gd-qr-slider' in content, 'the sliders have no live-readout hook'


def test_the_wizard_gained_the_quick_take_and_its_guidelines_route(client):
    """The visible product change: the wizard can now leave a public quick take, which means it also owes
    the reader the guidelines notice and a way to read them without losing the take."""
    profile = _hunter()
    content = _page(client, profile).content.decode()

    assert 'data-gd-qr-blurb' in content
    assert 'gd-qr__notice' in content
    assert 'data-gd-guidelines-open' in content
    assert 'id="gd-guidelines-modal"' in content, 'the notice links to a sheet that is not on the page'


def test_the_wizard_does_not_post_the_rating_itself():
    """It drives the shared RatingFields. A second POST site is how the two forms drifted apart."""
    src = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')

    assert '/rate/' not in src, 'the wizard posts a rating directly again'
    assert 'RatingFields.attach' in src, 'the wizard no longer uses the shared form controller'


def test_the_form_is_attached_once_not_per_game():
    """The wizard advances through a queue against ONE <form>. Re-attaching per game stacks a fresh set of
    listeners each time, and one submit posts as many times as you have rated."""
    src = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')

    assert src.count('RatingFields.attach') == 1
    assert 'setTarget(' in src, 'nothing re-points the form at the next game'


def test_the_shared_controller_loads_before_the_page_controller():
    """`quick-rate.js` defines RatingFields; load it after rate-my-games.js and the form is inert."""
    html = (ROOT / 'templates' / 'trophies' / 'rate_my_games.html').read_text(encoding='utf-8')

    assert html.index('js/quick-rate.js') < html.index('js/rate-my-games.js')


def test_a_rating_saves_with_no_quick_take(client):
    """The blurb is optional and must never gate the save -- this is a BULK flow, and most passes through
    it will be numbers only."""
    profile = _hunter()
    game = _ratable(profile)
    client.force_login(profile.user)

    resp = client.post(
        f'/api/v1/ratings/{game.concept_id}/group/default/rate/',
        {'difficulty': 5, 'grindiness': 4, 'hours_to_platinum': 30,
         'fun_ranking': 8, 'overall_rating': 4.0, 'blurb': ''},
        content_type='application/json',
    )

    assert resp.status_code == 200, resp.content
    rating = UserConceptRating.objects.get(profile=profile, concept=game.concept)
    assert rating.blurb == ''
    assert rating.hours_to_platinum == 30


# ── The queue ─────────────────────────────────────────────────────────────────────────────────────

def test_the_queue_serves_the_hunters_unrated_games(client):
    profile = _hunter()
    _ratable(profile, name='Bloodborne')
    client.force_login(profile.user)

    data = client.get(QUEUE).json()

    assert data['count'] == 1
    assert data['queue'][0]['unified_title']
    assert data['queue'][0]['trophy_group_id'] == 'default'


def test_a_rated_game_leaves_the_queue(client):
    """Which is why the wizard has no 'update your existing rating' branch: both queues serve only unrated
    items, so there is never a rating to prefill."""
    profile = _hunter()
    game = _ratable(profile)
    client.force_login(profile.user)
    assert client.get(QUEUE).json()['count'] == 1

    UserConceptRating.objects.create(
        profile=profile, concept=game.concept, concept_trophy_group=None,
        difficulty=5, grindiness=5, hours_to_platinum=10, fun_ranking=5, overall_rating=3,
    )

    assert client.get(QUEUE).json()['count'] == 0


def test_the_queue_query_count_does_not_grow_with_the_page(client):
    """Two sizes, same count. A hunter with a five-figure library pages this endpoint repeatedly, so a
    per-game query here is a per-game query multiplied by the whole queue."""
    def _measure(rows):
        profile = _hunter()
        for i in range(rows):
            _ratable(profile, name=f'Game {i:02d}')
        client.force_login(profile.user)
        client.get(QUEUE)                                  # warm session/auth
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(QUEUE).status_code == 200
        return len(ctx)

    small, full = _measure(2), _measure(12)

    assert small == full, f'query count grew with the queue: {small} -> {full}'


# ── The rebuild itself ────────────────────────────────────────────────────────────────────────────

def test_the_page_is_off_daisyui():
    """The rebuild test. Every one of these was on the old page, and each has a house primitive now:
    .pp-switch for the queue toggle, .pp-horizon for progress, .pp-bgal__chip for the opt-in,
    .pp-gbrowse__spinner for loading."""
    html = (ROOT / 'templates' / 'trophies' / 'rate_my_games.html').read_text(encoding='utf-8')
    # Strip the notes explaining the rebuild, which naturally name what they replaced.
    html = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', html, flags=re.S)

    for legacy in ('range-error', 'range-accent', 'progress-primary', 'checkbox-warning',
                   'loading-dots', 'input-secondary', 'badge-lg'):
        assert legacy not in html, f'{legacy} survived the rebuild'


def test_the_page_uses_the_shared_primitives():
    html = (ROOT / 'templates' / 'trophies' / 'rate_my_games.html').read_text(encoding='utf-8')

    for primitive in ('pp-switch', 'pp-horizon', 'pp-bgal__chip', 'pp-gbrowse__spinner',
                      'pp-head-cascade', 'pp-tally', 'pp-toolbar-card'):
        assert primitive in html, f'{primitive} is not used -- something was hand-rolled instead'


def test_the_header_counts_both_queues(client):
    """Base and DLC are separate queues, and a hunter who has cleared one still has the other waiting.
    Showing only the base count made the DLC tab look like a dead end."""
    profile = _hunter()

    ctx = _page(client, profile).context

    assert 'unrated_count' in ctx
    assert 'unrated_dlc_count' in ctx


def test_everything_the_wizard_hides_can_actually_be_hidden():
    """Tailwind's `.hidden` sits in `@layer utilities`; component CSS is @imported UNLAYERED, and unlayered
    beats layered whatever the specificity. So a component rule that sets `display` silently defeats the
    `.hidden` the controller toggles.

    This shipped in the first cut: a base-game card wearing a DLC pill AND a shovelware pill, because
    hiding them did nothing at all. Every element the JS hides that has a `display` of its own must have
    the utility restated."""
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')
    restated = set(re.findall(r'\.([a-z_-]+)\.hidden', css))

    # The elements the controller toggles that carry a display of their own. Add to this list whenever the
    # wizard learns to hide something new.
    for cls in ('rmg__prog', 'rmg__stats', 'rmg__stat', 'rmg__group', 'rmg__flag', 'gd-btn'):
        assert cls in restated, f'.{cls} is toggled hidden but keeps its own display'


def test_the_deal_motion_is_reduced_motion_gated():
    """The card-to-card transition is the page's one signature moment, so it is also the one thing here
    that has to collapse to a plain swap for anyone who asked for less movement."""
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')

    deal = css[css.index('@keyframes rmgDealOut'):]
    assert 'prefers-reduced-motion: no-preference' in deal, 'the deal animation is not gated'
    assert 'prefers-reduced-motion: reduce' in js, 'the JS still runs the timed swap under reduced motion'


def test_the_trophy_list_renderer_is_off_daisyui():
    """It is a SHARED renderer, so its markup is a primitive (.pp-trolist) rather than page classes."""
    src = (ROOT / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    body = src[src.index('const TrophyListRenderer'):src.index('SpoilerToggle')]

    assert 'pp-trolist__row' in body
    for legacy in ('badge badge-xs', 'text-base-content/', 'bg-success/10'):
        assert legacy not in body, f'{legacy} survived in the shared trophy renderer'


def test_the_field_partial_renders_standalone():
    """It is included by two hosts with different context; nothing in it may depend on a variable only one
    of them sets. `user_play_hours` is the optional one -- Game Detail SSRs it, the wizard fills it per
    game from JS."""
    bare = render_to_string('partials/_rating_fields.html', {})
    hinted = render_to_string('partials/_rating_fields.html', {'user_play_hours': 42})

    for name in FIELD_NAMES:
        assert f'name="{name}"' in bare
    assert "don't have your playtime" in bare
    assert '<b>42</b>' in hinted
