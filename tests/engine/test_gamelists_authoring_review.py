"""The Authoring review's fixes to Game Lists (docs/design/reviews/authoring.md, owner 2026-10-26).

AU-02 a list says what the READER has done with it: the browse cards' mark on each card, a "You: 3 of 8
      platinumed" line in the header, and "3 of 8 platinumed" on the My Lists cards.
AU-04 the browse tiles fetch thumbnail covers, not full-size ones.
AU-07 no "by you" on your own lists.
AU-08 your own list stays in My Pursuit (sub-nav and breadcrumb).
AU-09 edit mode has one way out.
AU-10 a published list can be shared, by anyone who can read it.
AU-11 no "Games" label in the toolbar.
"""
import re

import pytest
from django.urls import reverse

from gamelists.services import viewer_progress
from tests.engine._tree import ROOT
from tests.engine.test_gamelists_detail import _list, _staff, _url
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, ProfileGameFactory

pytestmark = pytest.mark.django_db
MINE = reverse('my_lists')


def _played(profile, concept, *, progress, has_plat=False, platinum=True, platforms=('PS5',)):
    game = GameFactory(concept=concept, title_platform=list(platforms),
                       defined_trophies={'bronze': 10, 'silver': 2, 'gold': 1, 'platinum': 1 if platinum else 0})
    return ProfileGameFactory(profile=profile, game=game, progress=progress, has_plat=has_plat)


# -- AU-02: the service -------------------------------------------------------------------------------------

def test_progress_picks_the_hunters_best_version_of_each_game():
    hunter = ProfileFactory()
    stacks, started, untouched = ConceptFactory(), ConceptFactory(), ConceptFactory()
    _played(hunter, stacks, progress=40, platforms=('PS4',))
    _played(hunter, stacks, progress=100, has_plat=True, platforms=('PS5',))
    _played(hunter, started, progress=12, platinum=False)

    got = viewer_progress.progress_by_concept(hunter, [stacks.pk, started.pk, untouched.pk])
    assert (got[stacks.pk].has_plat, got[stacks.pk].progress) == (True, 100), 'the platinumed stack wins'
    assert (got[started.pk].progress, got[started.pk].has_platinum) == (12, False)
    assert untouched.pk not in got, 'a game the hunter never touched has no state'


def test_nobody_reading_means_no_query_and_no_progress(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert viewer_progress.progress_by_concept(None, [1, 2]) == {}


def test_the_summary_is_absent_when_the_hunter_has_done_nothing():
    assert viewer_progress.summarise({}, 8) is None


# -- AU-02: the pages ---------------------------------------------------------------------------------------

def test_a_list_marks_the_readers_games_and_sums_them(client):
    owner = ProfileFactory(is_linked=True, psn_username='curator')
    game_list = _list(owner, games=3)
    reader = _staff(client, psn='reader')
    concepts = [item.concept for item in game_list.items.order_by('position')]
    ProfileGameFactory(profile=reader, game=concepts[0].games.first(), progress=100, has_plat=True)
    ProfileGameFactory(profile=reader, game=concepts[1].games.first(), progress=35)

    body = client.get(_url(game_list)).content.decode()
    assert 'pp-gcard__mark pp-gcard__mark--plat' in body and '>35%</span>' in body
    assert 'You: <b>1</b> of 3 platinumed &middot; <b>1</b> started' in body


def test_a_reader_who_has_played_none_of_it_sees_no_progress_line(client):
    owner = ProfileFactory(is_linked=True, psn_username='curator')
    game_list = _list(owner, games=2)
    _staff(client, psn='reader')
    body = client.get(_url(game_list)).content.decode()
    assert 'class="gl-you' not in body and 'pp-gcard__mark' not in body


def test_my_lists_cards_count_the_platinums(client):
    me = _staff(client, psn='me')
    game_list = _list(me, games=3, public=False)
    first = game_list.items.order_by('position').first().concept
    ProfileGameFactory(profile=me, game=first.games.first(), progress=100, has_plat=True)
    body = client.get(MINE).content.decode()
    assert '<b class="gl-card__done">1</b> of 3 platinumed' in body


def test_a_card_with_no_platinums_keeps_its_game_count(client):
    me = _staff(client, psn='me')
    _list(me, games=2)
    assert '2 games' in client.get(MINE).content.decode()


# -- AU-07 / AU-08: your own list ---------------------------------------------------------------------------

BYLINE = re.compile(r'by\s*<a href="[^"]*/curator/')


def test_your_own_list_has_no_byline_and_stays_in_my_pursuit(client):
    me = _staff(client, psn='curator')
    game_list = _list(me, games=1)
    resp = client.get(_url(game_list))
    assert not BYLINE.search(resp.content.decode()), 'the byline names you on your own list'
    assert (resp.context['hub_section'], resp.context['hub_subnav_active_slug']) == ('my_pursuit', 'my_lists')
    assert [c['text'] for c in resp.context['breadcrumb']][:2] == ['Home', 'My Lists']
    assert 'class="gl-card__author"' not in client.get(MINE).content.decode()


def test_somebody_elses_list_keeps_its_byline_and_community(client):
    owner = ProfileFactory(is_linked=True, psn_username='curator')
    game_list = _list(owner, games=1)
    _staff(client, psn='reader')
    resp = client.get(_url(game_list))
    assert BYLINE.search(resp.content.decode()), 'a reader lost the author byline'
    assert resp.context['hub_section'] == 'community'
    assert [c['text'] for c in resp.context['breadcrumb']][:2] == ['Home', 'Game Lists']


# -- AU-10: share -------------------------------------------------------------------------------------------

def test_a_published_list_can_be_shared_by_owner_reader_and_visitor(client):
    owner = ProfileFactory(is_linked=True, psn_username='curator')
    game_list = _list(owner, games=1)
    anon = client.get(_url(game_list)).content.decode()
    assert 'data-gl-share' in anon and f'/community/lists/{game_list.id}/' in anon

    _staff(client, psn='reader')
    assert client.get(_url(game_list)).content.decode().count('data-gl-share') == 1


def test_a_private_list_offers_no_share(client):
    me = _staff(client, psn='me')
    game_list = _list(me, games=1, public=False)
    body = client.get(_url(game_list)).content.decode()
    # The owner's public-state span renders hidden while private; the button inside it is the only one.
    assert body.count('data-gl-share') == 1
    assert 'data-gl-public-state hidden' in body


def test_share_uses_the_sheet_on_a_phone_and_the_clipboard_elsewhere():
    js = (ROOT / 'static' / 'js' / 'list-detail.js').read_text(encoding='utf-8')
    fn = js[js.index('function wireShare()'):js.index('function wireReport()')]
    assert "matchMedia('(pointer: coarse)')" in fn and 'navigator.share' in fn
    assert 'navigator.clipboard.writeText(url)' in fn
    assert 'wireShare();' in js[js.index('function boot('):]


# -- AU-09 / AU-11 / AU-04: chrome --------------------------------------------------------------------------

def test_edit_mode_has_one_way_out(client):
    me = _staff(client, psn='me')
    body = client.get(_url(_list(me, games=1))).content.decode()
    assert 'data-gl-edit-cancel' not in body, 'Cancel is back beside Save'
    js = (ROOT / 'static' / 'js' / 'list-detail.js').read_text(encoding='utf-8')
    assert 'identityHide = function () {' in js and 'reset(); }' in js[js.index('identityHide = function'):][:300]


def test_the_toolbar_has_no_games_label(client):
    me = _staff(client, psn='me')
    body = client.get(_url(_list(me, games=1))).content.decode()
    assert 'pp-ltoolbar__title' not in body


def test_browse_tiles_fetch_thumbnail_covers():
    """A quarter tile is ~120px wide, and the full-size cover cost the browse ~5.7 MB (review AU-04). Pinned on
    the templates: test games have no IGDB cover, so both sizes resolve to the same PSN art there."""
    tile = (ROOT / 'templates' / 'gamelists' / 'partials' / 'list_tile.html').read_text(encoding='utf-8')
    browse = (ROOT / 'templates' / 'gamelists' / 'browse.html').read_text(encoding='utf-8')
    for name, src in (('the tile', tile), ('the pick strip', browse)):
        assert '{{ item.display_image_url_small }}' in src, f'{name} fetches full-size covers again'
        assert '{{ item.display_image_url }}' not in src, f'{name} fetches full-size covers again'
