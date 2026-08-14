"""The profile's badges tab, rebuilt onto the LIVE badge system.

It had been reading `UserBadge` / `Badge` / `UserBadgeProgress` -- the legacy tables that nothing writes
any more -- so every visitor saw a frozen set. It now reads the same service the owner's Collection
gallery does, which is both the correct data and the whale-safe way to get it.
"""
import re
from pathlib import Path

import pytest

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}
SRC = ROOT / 'trophies' / 'views' / 'profile_views.py'


def test_the_tab_no_longer_reads_the_dead_badge_tables():
    """The whole point of the rebuild. Scoped to the badges builder rather than the file, because other
    parts of this module legitimately mention badges."""
    src = SRC.read_text(encoding='utf-8')
    builder = src[src.index('def _build_badges_tab_context'):]
    builder = builder[:builder.index('def _build_lists_tab_context')]
    builder = re.sub(r'"""[\s\S]*?"""', '', builder)   # the docstring NAMES the legacy tables it replaced

    for dead in ('UserBadge', 'UserBadgeProgress', 'Badge.objects'):
        assert dead not in builder, f'the badges tab is reading the legacy {dead} again'


def test_it_reads_the_same_service_the_collection_does(client):
    """Not a style preference: `build_collection_context` resolves progress from the materialized
    `group_progress` read-model. Live-evaluating badge state is O(engaged) and times out on a big account,
    which is the reason the Collection was built on that read-model in the first place."""
    profile = ProfileFactory(is_linked=True)

    response = client.get(f'/hunters/{profile.psn_username}/?tab=badges', **CF)

    assert response.status_code == 200
    assert 'list_badges' in response.context, 'the tab is not building collection context'
    assert 'summary' in response.context


def test_in_progress_badges_are_not_filtered_out():
    """A deliberate product call: what someone is CHASING is as interesting to a visitor as what they
    hold. The builder must not drop non-earned frames -- the medallion's state treatment already tells
    them apart, so a filter here would only hide them."""
    src = SRC.read_text(encoding='utf-8')
    builder = src[src.index('def _build_badges_tab_context'):]
    builder = builder[:builder.index('def _build_lists_tab_context')]

    assert "state'] == 'earned'" not in builder, 'the tab filters the wall down to earned badges'
    assert 'is_earned' not in builder


def test_every_offered_sort_can_actually_be_performed():
    """The invariant that matters here, because the obvious implementation is broken:
    `build_collection_context` does NOT sort -- its `sort` argument only seeds the gallery's client-side
    JS reorder. Passing the dropdown's value down to it would have reordered nothing at all. So the sort
    happens in the view, and the options offered must be exactly the ones it can apply."""
    from trophies.views.profile_views import ProfileDetailView

    # Sorted against a REAL frame, not an empty list: `sorted([])` never calls the key function, so an
    # empty list only proves the dict has the key, not that the sort can run.
    frames = [
        {'series_name': 'A', 'state': 'in_progress', 'progress_pct': 40,
         'rarity_pct': 3.2, 'earned_ts': 0, 'set_number': 1},
        {'series_name': 'B', 'state': 'earned', 'progress_pct': 100,
         'rarity_pct': 40, 'earned_ts': 500, 'set_number': 2},
    ]
    for value, _label in ProfileDetailView._BADGE_SORTS:
        assert len(ProfileDetailView._sort_badges(frames, value)) == 2, f'{value} dropped rows'


def test_an_unknown_sort_falls_back_instead_of_exploding(client):
    """The tab is public and crawled, so a hand-edited or stale `?sort=` must not 500."""
    profile = ProfileFactory(is_linked=True)

    response = client.get(f'/hunters/{profile.psn_username}/?tab=badges&sort=nonsense', **CF)

    assert response.status_code == 200
    assert response.context['sort'] == 'earned'


def test_rarity_sorts_the_no_data_value_last_not_first():
    """`rarity_pct` is the share of the community holding a badge, so LOW is rare -- but 0 means "nobody
    holds it yet", i.e. no data, not the rarest thing on the wall. Sorting naively ascending would lead
    the wall with every ungraded badge."""
    from trophies.views.profile_views import ProfileDetailView

    frames = [
        {'series_name': 'zero', 'rarity_pct': 0},
        {'series_name': 'rare', 'rarity_pct': 0.4},
        {'series_name': 'common', 'rarity_pct': 55},
    ]

    order = [f['series_name'] for f in ProfileDetailView._sort_badges(frames, 'rarity')]

    assert order == ['rare', 'common', 'zero']


def test_progress_leads_with_what_is_closest_to_being_earned():
    """An earned badge has nothing left to chase and an untouched one has no progress to rank by, so
    neither belongs in the middle of a "closest to earning" ordering."""
    from trophies.views.profile_views import ProfileDetailView

    frames = [
        {'series_name': 'done', 'state': 'earned', 'progress_pct': 100},
        {'series_name': 'far', 'state': 'in_progress', 'progress_pct': 10},
        {'series_name': 'near', 'state': 'in_progress', 'progress_pct': 90},
    ]

    order = [f['series_name'] for f in ProfileDetailView._sort_badges(frames, 'progress')]

    assert order[:2] == ['near', 'far'], 'in-progress badges do not lead the "closest to earning" sort'


def test_recently_earned_puts_unearned_at_the_end_not_interleaved():
    """Unearned frames carry `earned_ts` 0. Under a plain descending sort they gather at the end, which is
    what a visitor wants from "recently earned" -- but it is worth pinning, since a signed-off-by-zero bug
    here would scatter them through the held badges."""
    from trophies.views.profile_views import ProfileDetailView

    frames = [
        {'series_name': 'never', 'earned_ts': 0},
        {'series_name': 'old', 'earned_ts': 100},
        {'series_name': 'new', 'earned_ts': 900},
    ]

    order = [f['series_name'] for f in ProfileDetailView._sort_badges(frames, 'earned')]

    assert order == ['new', 'old', 'never']


def test_the_tab_does_not_borrow_the_collection_modal():
    """The sharp edge of reusing the Collection here. `CollectionBadgeModalView` is login-gated and renders
    `request.user.profile` -- so on someone ELSE's profile it would open a badge and show the VISITOR their
    own progress on that series. Cards link to the profile-scoped badge detail page instead."""
    tab = (ROOT / 'templates/trophies/partials/profile_detail/tabs/badges_tab.html').read_text(encoding='utf-8')
    # Comments stripped first: the template's own comment NAMES the modal to explain why it is not used
    # here, so a raw substring check passes or fails on the prose rather than on the markup.
    markup = re.sub(r'{%\s*comment\s*%}[\s\S]*?{%\s*endcomment\s*%}', '', tab)

    assert 'collection_badge_modal' not in markup
    assert 'badge_detail_with_profile' in markup, 'badge cards do not link to the profile-scoped detail page'


def test_the_wall_renders_the_shared_medallion(client):
    """The badge is the Medallion everywhere it appears. This tab borrows the gallery's CARD vocabulary
    (medallion, earned tick, caption) without its toolbar -- the tab strip above already is that chrome."""
    profile = ProfileFactory(is_linked=True)

    body = client.get(f'/hunters/{profile.psn_username}/?tab=badges', **CF).content.decode()

    assert 'pp-gallery__card' in body or 'pp-gallery__empty' in body
    assert 'pp-gallery__chip' not in body, "the gallery's filter toolbar was dragged in with the cards"


def test_a_private_profile_does_not_serve_its_badges_over_htmx(client):
    """Gaming history is opt-out, and the ONLY thing enforcing it was `{% if profile.psn_history_public %}`
    in profile_detail.html. HTMX requests are answered with the TAB template directly, which never renders
    that parent -- so the guard was skipped entirely on that path.

    It was dormant for badges while the tab read tables nothing writes; repointing it at real data made it
    live. Badge progress is a projection of trophy history, which is exactly what the flag hides.
    """
    profile = ProfileFactory(is_linked=True, psn_history_public=False)

    response = client.get(
        f'/hunters/{profile.psn_username}/?tab=badges', HTTP_HX_REQUEST='true', **CF
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert 'pp-gallery__card' not in body, "a private profile's badge wall is served over HTMX"
    # The full page is returned instead of the tab body, and profile_detail.html's own guard then drops
    # the whole tab region. Asserted on the CONTAINER the guard wraps -- `tab-results` alone also appears
    # in the page's inline JS, so it matches a string that is present either way.
    assert 'id="tab-content"' not in body, 'the tab region rendered for a profile that opted out'


def test_a_private_profile_builds_no_tab_context_at_all(client):
    """Not just hidden -- not BUILT. A wall assembled and then dropped by a template guard is pure cost on
    a page that will never show it."""
    profile = ProfileFactory(is_linked=True, psn_history_public=False)

    response = client.get(f'/hunters/{profile.psn_username}/?tab=badges', **CF)

    assert response.status_code == 200
    assert 'list_badges' not in response.context


def test_an_earned_badge_caption_says_what_it_took_and_when(client):
    """"5 stages - Mar 02, 2026", not the date alone.

    The meter is full on every earned card, so the stage count is what separates a five-stage badge from a
    fifteen-stage one -- the date says nothing about the badge itself. Worded to match the Collection
    gallery so the same badge reads the same on both walls.
    """
    from tests.engine.test_collection_service import _hold, _series, _standing

    profile = ProfileFactory(is_linked=True)
    _, groups = _series('rs-cap')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-cap', group_progress={'ultra-hd': [5, 5]})

    body = client.get(f'/hunters/{profile.psn_username}/?tab=badges', **CF).content.decode()

    assert '5 stages' in body, 'an earned caption does not say how many stages the badge took'
    # The edition has its own line ABOVE the stat, in its tier colour -- the two facts stopped competing
    # for one line. Asserted by ORDER, not by a substring at a fixed offset: the first cut pinned exact
    # template indentation, so re-indenting the file would have passed while the regression was live.
    assert 'pp-gallery__edition' in body and 'Ultra HD' in body
    caption = body[body.index('pp-gallery__edition'):]
    assert caption.index('Ultra HD') < caption.index('pp-gallery__stat'), (
        'the edition is not on its own line above the stat'
    )


def test_the_badges_tab_gets_the_same_card_treatment_as_the_collection(client):
    """The medallion half of the change reached this surface too.

    All of this was only ever asserted through the collection gallery template, so the tab could have
    drifted silently -- which is how it ended up on the legacy badge tables in the first place.
    """
    from tests.engine.test_collection_service import _hold, _series, _standing

    profile = ProfileFactory(is_linked=True)
    _, groups = _series('rs-tab')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-tab', group_progress={'ultra-hd': [5, 5], 'legacy-hd': [0, 4]})

    body = client.get(f'/hunters/{profile.psn_username}/?tab=badges', **CF).content.decode()

    assert 'pp-med__meter' in body, 'the permanent bar did not reach the profile tab'
    assert body.count('is-full') == 1, 'the full marking is missing or not specific to the completed bar'
    assert 'pp-med__count' not in body, 'the count under the bar repeats the caption here'
    assert 'pp-gallery__edition' in body
    assert 'Legacy HD' not in body, 'the untouched edition was not filtered on this surface'
