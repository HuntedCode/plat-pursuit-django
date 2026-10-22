"""The Game page hero, rebuilt under the Page Principles (DS-07, DS-09, DS-13 in docs/design/reviews/discovery.md).

- The signed-in hunter's status, from their BEST list (owner, 2026-10-22): plat on any list wins, else the
  highest percentage. Signed out, no card.
- The platinum's rarity: that list's platinum, else the most-played list's (owner, 2026-10-22). Left out with
  no platinum, or before PSN has reported a rate.
- One line of facts; Share (once finished), Add to list and Report on the first screen.
- The list picker tells identical-looking lists apart; the About tab takes the screenshots and the family.
"""
import datetime

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from core.services import completion_card_service as cards
from tests.factories import (
    CompanyFactory, ConceptCompanyFactory, ConceptFactory, EarnedTrophyFactory, GameFactory, ProfileFactory,
    ProfileGameFactory, ProfileTrophyGroupFactory, TrophyFactory, TrophyGroupFactory,
)
from trophies.models import IGDBMatch
from trophies.views.game_page_views import disambiguate_entries

pytestmark = pytest.mark.django_db

PLAT = {'bronze': 30, 'silver': 10, 'gold': 6, 'platinum': 1}
NO_PLAT = {'bronze': 12, 'silver': 3, 'gold': 1, 'platinum': 0}


def _page(igdb_id, *platforms, defined=PLAT, played=None):
    """One concept per list, all on one igdb page (the split-concept shape), in the given platform order."""
    games = []
    for i, plats in enumerate(platforms):
        concept = ConceptFactory(unified_title='Hero Game')
        IGDBMatch.objects.create(concept=concept, igdb_id=igdb_id, status='accepted')
        games.append(GameFactory(concept=concept, title_name='Hero Game', title_platform=list(plats), defined_trophies=dict(defined),
                                 played_count=(played or [0] * len(platforms))[i]))
    return f'/games/{igdb_id}/', games


def _plat_trophy(game, rate, rarity):
    return TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=rate, trophy_rarity=rarity)


def _hunter(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    return profile


def _hero(client, url):
    html = client.get(url).content.decode()
    start = html.index('class="gd-hero gp-hero')
    return html[start:html.index('</section>', start)]


# ── The hunter's status ───────────────────────────────────────────────────────────────────────────

def test_signed_out_there_is_no_status_card(client):
    url, _ = _page(940001, ['PS5'])
    assert 'gp-status' not in _hero(client, url)


def test_a_game_the_hunter_does_not_own(client):
    url, _ = _page(940002, ['PS5'])
    _hunter(client)

    hero = _hero(client, url)

    assert 'gp-status--none' in hero and 'Not in your library yet' in hero


def test_in_progress_reads_percent_and_what_is_left(client):
    url, (game,) = _page(940003, ['PS5'])
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=game, progress=45, earned_trophies_count=23)

    hero = _hero(client, url)

    assert 'gp-status--started' in hero
    assert '45% &middot; 24 trophies to go' in hero      # 47 defined, 23 earned
    assert '23 of 47 earned' in hero
    assert 'gp-status__list' not in hero, 'one list on the page: no list label'


def test_owned_with_nothing_earned(client):
    url, (game,) = _page(940004, ['PS5'])
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=game, progress=0)

    hero = _hero(client, url)

    assert 'gp-status--owned' in hero and 'In your library' in hero and '47 trophies to earn' in hero


def test_a_platinum_on_any_list_beats_a_higher_percentage_elsewhere(client):
    """Their BEST list: the PS4 platinum (at 92% after DLC) outranks 96% on PS5, and the card names the list."""
    url, (ps5, ps4) = _page(940005, ['PS5'], ['PS4'])
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=ps5, progress=96)
    ProfileGameFactory(profile=hunter, game=ps4, progress=92, has_plat=True)
    when = timezone.make_aware(datetime.datetime(2026, 3, 9, 12, 0))
    EarnedTrophyFactory(profile=hunter, trophy=_plat_trophy(ps4, 9.1, 1), earned_date_time=when)

    hero = _hero(client, url)

    assert 'gp-status--plat' in hero and 'You platinumed this' in hero
    assert 'gp-status__list">PS4<' in hero
    assert 'Mar 9, 2026' in hero and '92% complete' in hero


def test_without_a_platinum_the_highest_percentage_wins(client):
    url, (ps5, ps4) = _page(940006, ['PS5'], ['PS4'])
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=ps5, progress=10, earned_trophies_count=5)
    ProfileGameFactory(profile=hunter, game=ps4, progress=60, earned_trophies_count=28)

    hero = _hero(client, url)

    assert '60% &middot; 19 trophies to go' in hero and 'gp-status__list">PS4<' in hero


# ── Share ────────────────────────────────────────────────────────────────────────────────────────

def test_share_appears_once_the_list_is_finished(client):
    url, (game,) = _page(940007, ['PS5'])
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=game, progress=100, has_plat=True)
    EarnedTrophyFactory(profile=hunter, trophy=_plat_trophy(game, 20.0, 2))
    group = TrophyGroupFactory(game=game, trophy_group_id='default')
    ProfileTrophyGroupFactory(profile=hunter, trophy_group=group, progress=100)

    hero = _hero(client, url)

    assert f'href="/shareables/?c={group.pk}"' in hero and '>Share' in hero


def test_no_share_while_in_progress(client):
    url, (game,) = _page(940008, ['PS5'])
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=game, progress=50)
    group = TrophyGroupFactory(game=game, trophy_group_id='default')
    ProfileTrophyGroupFactory(profile=hunter, trophy_group=group, progress=50)

    assert '/shareables/' not in _hero(client, url)


def test_share_link_reads_the_group_or_the_whole_game():
    """The helper the two pages share: the default group at 100, or (a stale group row) the game at 100."""
    hunter = ProfileFactory(is_linked=True)
    game = GameFactory(defined_trophies=dict(PLAT))
    group = TrophyGroupFactory(game=game, trophy_group_id='default')
    ProfileTrophyGroupFactory(profile=hunter, trophy_group=group, progress=80)

    assert cards.share_link(hunter, game, 80) is None
    assert cards.share_link(hunter, game, 100) == {'url': f'/shareables/?c={group.pk}', 'variant': 'platinum'}

    other = GameFactory(defined_trophies=dict(NO_PLAT))
    assert cards.share_link(hunter, other, 100) is None, 'no group row: no card'


# ── Rarity ───────────────────────────────────────────────────────────────────────────────────────

def test_rarity_reads_the_hunters_own_list(client):
    url, (ps5, ps4) = _page(940009, ['PS5'], ['PS4'], played=[500, 10])
    _plat_trophy(ps5, 30.0, 2)
    _plat_trophy(ps4, 4.2, 0)
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=ps4, progress=20)

    hero = _hero(client, url)

    assert 'data-rarity="mythic"' in hero and '4.2%' in hero
    assert 'Ultra Rare PS4 platinum' in hero


def test_rarity_falls_back_to_the_most_played_list(client):
    url, (ps5, ps4) = _page(940010, ['PS5'], ['PS4'], played=[10, 500])
    _plat_trophy(ps5, 30.0, 2)
    _plat_trophy(ps4, 9.1, 1)

    hero = _hero(client, url)

    assert 'Very Rare PS4 platinum' in hero and '9.1%' in hero and 'data-rarity="rare"' in hero


def test_rarity_is_left_out_without_a_platinum_or_a_rate(client):
    url, _ = _page(940011, ['PS5'], defined=NO_PLAT)
    assert 'gp-rarity' not in _hero(client, url)

    url, (game,) = _page(940012, ['PS5'])
    _plat_trophy(game, 0.0, 2)          # a tier but no rate yet: 0.0 is the unsynced default, not a real 0%
    assert 'gp-rarity' not in _hero(client, url)

    url, (game,) = _page(940026, ['PS5'])
    _plat_trophy(game, 7.5, None)       # a rate but no tier
    assert 'gp-rarity' not in _hero(client, url)


def test_one_list_names_no_platform_in_the_rarity(client):
    url, (game,) = _page(940013, ['PS5'])
    _plat_trophy(game, 51.0, 3)

    hero = _hero(client, url)

    assert 'Common platinum' in hero and 'data-rarity="common"' in hero


# ── Facts and actions ─────────────────────────────────────────────────────────────────────────────

def test_one_line_of_facts(client):
    url, (game,) = _page(940014, ['PS5'], played=[3])
    game.concept.release_date = timezone.make_aware(datetime.datetime(2019, 5, 1))
    game.concept.save()
    ConceptCompanyFactory(company=CompanyFactory(name='Hero Studio', slug='hero-studio'), concept=game.concept,
                          is_developer=True, is_publisher=False)

    hero = _hero(client, url)
    facts = hero[hero.index('pp-factline gp-meta'):]
    facts = facts[:facts.index('</p>')]

    assert '>Hero Studio</a>' in facts and '>2019</time>' in facts and '3 players' in facts
    assert 'gd-hero__facts' not in hero and 'Publisher' not in hero, 'the full credits live in About'


def test_actions_need_a_linked_hunter(client):
    url, _ = _page(940015, ['PS5'])
    assert 'gp-actions' not in _hero(client, url)

    _hunter(client)
    hero = _hero(client, url)

    assert 'data-quick-add' in hero and 'data-flag-open' in hero
    assert 'Add to list' in hero and 'Report<span class="gp-actions__long"> an issue</span>' in hero


def test_add_to_list_left_the_tabs_row(client):
    url, _ = _page(940016, ['PS5'])
    _hunter(client)
    html = client.get(url).content.decode()

    tabs = html[html.index('id="gp-tabs-row"'):html.index('id="gp-minibar-sentinel"')]
    assert 'data-quick-add' not in tabs


def test_a_game_with_no_side_panels_drops_the_side_column(client):
    """Rule 3: no "Not in a badge series" / "Not on the Job Board" placeholders. With no platinum, badge or
    contract there is nothing for the side column, so the grid drops it (the noside modifier) rather than
    leaving an empty third column."""
    url, _ = _page(940017, ['PS5'], defined=NO_PLAT)

    hero = _hero(client, url)

    assert 'gp-hero__grid--noside' in hero
    assert 'gp-hero__side' not in hero and 'gd-spine__row' not in hero


def test_one_side_panel_keeps_the_column(client):
    url, (game,) = _page(940019, ['PS5'])
    _plat_trophy(game, 12.0, 2)

    hero = _hero(client, url)

    assert 'gp-hero__grid--noside' not in hero and 'class="gp-hero__side"' in hero


def test_finished_without_a_platinum_reads_finished(client):
    """100% on a list with no platinum is finished, not "100% · 0 trophies to go"."""
    url, (game,) = _page(940020, ['PS5'], defined=NO_PLAT)
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=game, progress=100, earned_trophies_count=16)

    hero = _hero(client, url)

    assert 'gp-status--complete' in hero and 'You finished this' in hero
    assert 'to go' not in hero


def test_trophies_earned_at_a_floored_zero_percent_is_started(client):
    """PSN floors a sliver of progress to 0%; trophies earned still means started, not "In your library"."""
    url, (game,) = _page(940021, ['PS5'])
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=game, progress=0, earned_trophies_count=1)

    assert 'gp-status--started' in _hero(client, url)


def test_a_stale_earned_count_shows_no_counts(client):
    """An earned count above the list's total would read "0 trophies to go" at 37%: the counts are dropped."""
    url, (game,) = _page(940022, ['PS5'])
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=game, progress=37, earned_trophies_count=60)

    hero = _hero(client, url)

    assert '37%' in hero and 'to go' not in hero and ' earned</p>' not in hero


def test_rarity_falls_back_when_the_hunters_list_has_no_platinum(client):
    """Their best list (PS5, no platinum) has no rate to show, so the panel reads the most-played list that has one."""
    url, (ps5, ps4, ps3) = _page(940023, ['PS5'], ['PS4'], ['PS3'], played=[900, 20, 300])
    ps5.defined_trophies = dict(NO_PLAT)
    ps5.save(update_fields=['defined_trophies'])
    _plat_trophy(ps4, 30.0, 2)
    _plat_trophy(ps3, 6.0, 1)
    hunter = _hunter(client)
    ProfileGameFactory(profile=hunter, game=ps5, progress=50)

    hero = _hero(client, url)

    assert 'Very Rare PS3 platinum' in hero and '6.0%' in hero


def test_the_facts_line_without_a_developer(client):
    url, _ = _page(940024, ['PS5'], played=[2])

    hero = _hero(client, url)
    facts = hero[hero.index('pp-factline gp-meta'):]
    facts = facts[:facts.index('</p>')]

    assert 'gp-meta__dev' not in facts and '2 players' in facts


def test_family_rows_link_game_pages_on_a_concept_page_too(client):
    """The c/ route keeps Other platforms in About; its family rows still link each sibling's GAME page, as the
    hero band did on every page."""
    from trophies.models import GameFamily

    family = GameFamily.objects.create(igdb_id=626262, canonical_name='C Family')
    home = ConceptFactory(concept_id='PP_CHOME', unified_title='C Home', family=family)
    GameFactory(concept=home, title_platform=['PS4'])
    sib = ConceptFactory(unified_title='C Sibling', family=family)
    IGDBMatch.objects.create(concept=sib, igdb_id=940025, status='accepted')
    GameFactory(concept=sib, title_platform=['PS5'])

    html = client.get('/games/c/PP_CHOME/', HTTP_CF_RAY='test').content.decode()

    assert 'href="/games/940025/"' in html


# ── The list picker (DS-09) ───────────────────────────────────────────────────────────────────────

def _entry(name, platforms, total, np, regions=None):
    game = GameFactory.build(title_platform=platforms, np_communication_id=np, is_regional=bool(regions),
                             region=regions or [], defined_trophies={'bronze': total})
    return {'game': game, 'name': name}


def test_identical_chips_get_a_trophy_count_then_a_list_id():
    a = _entry('Zero Zero', ['PSVITA'], 20, 'NPWR00001_00')
    b = _entry('Zero Zero', ['PSVITA'], 20, 'NPWR00002_00')
    c = _entry('Zero Zero', ['PSVITA'], 35, 'NPWR00003_00')
    d = _entry('Zero Zero', ['PS4'], 20, 'NPWR00004_00')            # its platform already tells it apart
    e = _entry('Zero Zero', ['PSVITA'], 20, 'NPWR00005_00', ['EU'])  # and its region

    disambiguate_entries([a, b, c, d, e])

    assert a['detail'] == '20 trophies · NPWR00001_00'
    assert b['detail'] == '20 trophies · NPWR00002_00'
    assert c['detail'] == '35 trophies'
    assert 'detail' not in d and 'detail' not in e


def test_the_picker_renders_the_detail(client):
    url, _ = _page(940018, ['PS4'], ['PS4'])
    html = client.get(url).content.decode()

    assert html.count('class="gp-lchip__detail"') == 2


# ── About (DS-07, DS-13) ──────────────────────────────────────────────────────────────────────────

def test_about_takes_the_screenshots():
    html = render_to_string('trophies/partials/game_detail/game_about_card.html', {
        'game': GameFactory(), 'image_urls': {'screenshot_urls': [f'https://img/{i}.jpg' for i in range(8)]},
        'page_title': 'Shots', 'about_hide_other_platforms': True,
    })

    assert 'id="gd-shots"' in html
    assert html.count('data-shot=') == 6 and '+2' in html
    assert 'No extended info yet' not in html, 'screenshots are something to show'
