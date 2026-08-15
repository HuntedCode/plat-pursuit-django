"""The profile Ratings tab: a hunter's taste, and what it costs to show it.

The properties pinned here are mostly the ones a page that merely renders would never reveal -- that the
query count does not follow the size of the wall, that a DLC rating is compared against the DLC's own
community and not its base game's, and that the shared summary sentence keeps working when it is pointed
at a person instead of a game.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from trophies.models import UserConceptRating
from trophies.services.rating_service import (
    PROFILE_RATING_SORTS,
    build_profile_ratings_page,
    profile_rating_summary,
)
from tests.factories import (
    ConceptFactory,
    ConceptTrophyGroupFactory,
    GameFactory,
    IGDBMatchFactory,
    ProfileFactory,
    ProfileGameFactory,
    TrophyFactory,
    UserConceptRatingFactory,
)

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

#: The origin guard rejects requests without it, so every page fetch carries one.
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _rated(profile, title='Test Game', owned=True, **scores):
    """One rating, with the Game behind it that the card's cover and link need."""
    concept = ConceptFactory(unified_title=title)
    if owned:
        ProfileGameFactory(profile=profile, game=GameFactory(concept=concept), has_plat=True, progress=100)
    return UserConceptRatingFactory(profile=profile, concept=concept, **scores)


def _url(profile, **params):
    query = ''.join(f'&{k}={v}' for k, v in params.items())
    return f'/hunters/{profile.psn_username}/?tab=ratings{query}'


# --------------------------------------------------------------------------- #
#  The summary
# --------------------------------------------------------------------------- #

def test_summary_aggregates_the_whole_set_not_the_page():
    """The wall is paged; the summary is not. It describes everything they have rated."""
    profile = ProfileFactory(is_linked=True)
    for i in range(30):
        _rated(profile, title=f'Game {i}', overall_rating=4.0, hours_to_platinum=10)

    summary = profile_rating_summary(profile)

    assert summary['count'] == 30
    assert summary['hours'] == 300
    assert summary['avg_rating'] == pytest.approx(4.0)


def test_summary_counts_only_takes_that_would_actually_show():
    """Counted through the same predicate `visible_blurbs()` reads by. A header promising four quick takes
    over a wall that renders three is a bug you only find by counting both."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='A', blurb='Loved it')
    _rated(profile, title='B', blurb='Hated it', blurb_hidden=True)
    _rated(profile, title='C')      # no blurb at all

    assert profile_rating_summary(profile)['takes'] == 1


def test_the_summary_reports_the_extreme_not_a_fourth_average():
    """The synthesized sentence already carries the difficulty, grindiness and fun averages, so the cell
    beside it reports what an average hides: the hardest thing they have signed off on."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Easy', difficulty=2)
    _rated(profile, title='Easy too', difficulty=2)
    _rated(profile, title='The wall', difficulty=10)

    summary = profile_rating_summary(profile)

    assert summary['toughest'] == 10
    assert summary['avg_difficulty'] < 5      # the average would have buried it


def test_the_recommend_rate_is_denominated_in_ANSWERED_ratings():
    """Everything scored before the recommendation existed carries no answer. Counting those against a
    hunter would read as someone who recommends almost nothing -- for as long as their backlog takes to
    clear, which for a prolific rater is a long time."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Yes', recommendation='worth_it')
    _rated(profile, title='Also yes', recommendation='worth_it')
    _rated(profile, title='Mixed', recommendation='good_game_bad_plat')   # NOT a plat recommendation
    for i in range(6):
        _rated(profile, title=f'Legacy {i}')            # no recommendation at all

    summary = profile_rating_summary(profile)

    assert summary['count'] == 9
    assert summary['answered'] == 3
    assert summary['recommend_pct'] == 67       # 2 of 3 ANSWERED, not 2 of 9


def test_a_hunter_who_has_answered_nothing_has_no_rate():
    """`None`, not zero -- "recommends 0%" is a verdict, and they have not given one."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Legacy')

    assert profile_rating_summary(profile)['recommend_pct'] is None


def test_summary_of_an_unrated_hunter_is_empty_not_an_error():
    profile = ProfileFactory(is_linked=True)

    summary = profile_rating_summary(profile)

    assert summary['count'] == 0
    assert summary['avg_rating'] is None


def test_the_summary_is_one_query():
    """Never a Python pass over the rows. A prolific rater has thousands, and building totals by iterating
    a profile-scoped queryset is the shape that OOMs a big account."""
    profile = ProfileFactory(is_linked=True)
    for i in range(12):
        _rated(profile, title=f'Game {i}')

    with CaptureQueriesContext(connection) as ctx:
        profile_rating_summary(profile)

    assert len(ctx.captured_queries) == 1


# --------------------------------------------------------------------------- #
#  The wall
# --------------------------------------------------------------------------- #

def test_the_page_attaches_the_game_behind_each_concept():
    """A rating hangs off a Concept, but a cover and a link need a Game."""
    profile = ProfileFactory(is_linked=True)
    rating = _rated(profile, title='Bloodborne')

    row = build_profile_ratings_page(profile)[0]

    assert row.pk == rating.pk
    assert row.card_game is not None
    assert row.card_game.concept_id == rating.concept_id


def test_a_concept_with_no_owned_game_still_renders():
    """Possible after a concept merge re-points a rating onto a survivor they own no copy of. The card
    loses its LINK, not its existence."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Orphaned', owned=False)

    row = build_profile_ratings_page(profile)[0]

    assert row.card_game is None


def test_the_platinumed_version_is_the_one_linked():
    """A concept can span several platform SKUs. The pick is ordered, not arbitrary -- otherwise a card
    changes which version it links to between two loads of the same page."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Multi-platform')
    ps4 = GameFactory(concept=concept, np_communication_id='NPWR00001_00')
    ps5 = GameFactory(concept=concept, np_communication_id='NPWR00002_00')
    ProfileGameFactory(profile=profile, game=ps4, has_plat=False, progress=40)
    ProfileGameFactory(profile=profile, game=ps5, has_plat=True, progress=100)
    UserConceptRatingFactory(profile=profile, concept=concept)

    row = build_profile_ratings_page(profile)[0]

    assert row.card_game.pk == ps5.pk


def test_query_count_does_not_follow_the_size_of_the_wall():
    """Four queries flat: the ratings, the community scores, the games, and which of those concepts define
    a platinum (for the verdict wording). The wall is the one place an N+1 is invisible in review and
    obvious in production."""
    profile = ProfileFactory(is_linked=True)
    for i in range(20):
        _rated(profile, title=f'Game {i}')

    with CaptureQueriesContext(connection) as ctx:
        rows = build_profile_ratings_page(profile)
        # Touch everything a card draws, so a lazy relation would fire here rather than in the template.
        [(r.card_game.display_image_url, r.concept.unified_title, r.community_avg) for r in rows]

    assert len(rows) == 20
    assert len(ctx.captured_queries) == 4, (
        'the builder is documented as four queries flat on the base wall -- a fifth is a decision, not '
        'an accident'
    )


def test_an_unrated_hunter_gets_an_empty_wall_not_an_error():
    profile = ProfileFactory(is_linked=True)

    assert build_profile_ratings_page(profile) == []


# --------------------------------------------------------------------------- #
#  Comparison against the community
# --------------------------------------------------------------------------- #

def test_the_community_score_excludes_a_sample_of_one():
    """"You 4.5, community 4.5" against a single rater is the same number printed twice with a "vs" between
    them. It is withheld rather than shown as agreement."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Only rater', overall_rating=4.5)

    row = build_profile_ratings_page(profile)[0]

    assert row.community_avg is None


def test_the_community_score_is_the_average_of_everyone():
    profile = ProfileFactory(is_linked=True)
    rating = _rated(profile, title='Popular', overall_rating=5.0)
    UserConceptRatingFactory(concept=rating.concept, overall_rating=3.0)
    UserConceptRatingFactory(concept=rating.concept, overall_rating=1.0)

    row = build_profile_ratings_page(profile)[0]

    assert row.community_avg == pytest.approx(3.0)
    assert row.community_n == 3


def test_a_dlc_rating_is_compared_against_that_dlc_not_the_base_game():
    """The trap the shared `annotate_community_ratings` helper walks into: it correlates on the concept
    alone and hard-filters to base-game rows, so a DLC rating would be scored against the base game's
    community -- a comparison that renders convincingly and means something else entirely."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Has DLC')
    ProfileGameFactory(profile=profile, game=GameFactory(concept=concept))
    dlc = ConceptTrophyGroupFactory(concept=concept, trophy_group_id='001', display_name='The Old Hunters')

    # The base game is beloved; the DLC is not. Both by other people.
    UserConceptRatingFactory(concept=concept, concept_trophy_group=None, overall_rating=5.0)
    UserConceptRatingFactory(concept=concept, concept_trophy_group=None, overall_rating=5.0)
    UserConceptRatingFactory(concept=concept, concept_trophy_group=dlc, overall_rating=2.0)
    UserConceptRatingFactory(profile=profile, concept=concept, concept_trophy_group=dlc, overall_rating=2.0)

    row = build_profile_ratings_page(profile, dlc=True)[0]

    assert row.concept_trophy_group_id == dlc.pk
    assert row.community_avg == pytest.approx(2.0)


def test_a_base_game_rating_finds_its_community_despite_the_null_group():
    """`NULL = NULL` never matches in SQL, so correlating on the group would silently leave every base-game
    row unmatched -- the reason the pairing happens in Python rather than in a subquery."""
    profile = ProfileFactory(is_linked=True)
    rating = _rated(profile, title='Base only', overall_rating=4.0)
    UserConceptRatingFactory(concept=rating.concept, concept_trophy_group=None, overall_rating=2.0)

    row = build_profile_ratings_page(profile)[0]

    assert row.concept_trophy_group_id is None
    assert row.community_avg == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
#  Sorting + paging
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('sort', [value for value, _ in PROFILE_RATING_SORTS])
def test_every_offered_sort_is_implemented(sort):
    """The control's options come from the same list the service orders by, so it cannot advertise a sort
    that reorders nothing -- the drift that once put a dead sort on the hunters page."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='A')
    _rated(profile, title='B')

    assert len(build_profile_ratings_page(profile, sort=sort)) == 2


def test_highest_and_lowest_are_opposite_ends_of_the_same_shelf():
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Great', overall_rating=5.0)
    _rated(profile, title='Awful', overall_rating=1.0)

    assert build_profile_ratings_page(profile, sort='highest')[0].overall_rating == 5.0
    assert build_profile_ratings_page(profile, sort='lowest')[0].overall_rating == 1.0


def test_hardest_and_longest_rank_by_their_own_axis():
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Brutal but short', difficulty=10, hours_to_platinum=5)
    _rated(profile, title='Easy but endless', difficulty=1, hours_to_platinum=400)

    assert build_profile_ratings_page(profile, sort='hardest')[0].difficulty == 10
    assert build_profile_ratings_page(profile, sort='longest')[0].hours_to_platinum == 400


def test_an_unknown_sort_falls_back_rather_than_raising():
    """`?sort=` is a public, crawled, hand-editable URL."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='A')

    assert len(build_profile_ratings_page(profile, sort='; DROP TABLE')) == 1


def test_paging_neither_repeats_nor_skips_a_card_when_scores_tie():
    """An OFFSET page over tied values can return a row twice unless the ordering is a total one -- and
    scores tie constantly here (a 1-10 integer over a few hundred rows)."""
    profile = ProfileFactory(is_linked=True)
    for i in range(10):
        _rated(profile, title=f'Game {i}', overall_rating=4.0, difficulty=5)

    first = build_profile_ratings_page(profile, sort='highest', page=1, per_page=4)
    second = build_profile_ratings_page(profile, sort='highest', page=2, per_page=4)
    third = build_profile_ratings_page(profile, sort='highest', page=3, per_page=4)

    seen = [r.pk for r in first + second + third]
    assert len(seen) == 10
    assert len(set(seen)) == 10


# --------------------------------------------------------------------------- #
#  The page
# --------------------------------------------------------------------------- #

def test_the_tab_renders_the_wall_and_the_summary(client):
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Hollow Knight', overall_rating=5.0, blurb='Still thinking about it.')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'pp-rwall' in body
    assert 'pp-taste' in body
    assert 'Hollow Knight' in body
    assert 'Still thinking about it.' in body


def test_the_card_shows_the_whole_rating_not_a_subset(client):
    """All five scored axes: the overall as stars, and difficulty / grindiness / fun / hours as cells. An
    earlier cut showed three of them on the argument that five numbers is a spreadsheet -- true of a narrow
    portrait card, and the reason the card is wide now instead of the rating being trimmed to fit it."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Everything', difficulty=8, grindiness=6, fun_ranking=9,
           hours_to_platinum=62, overall_rating=4.5)

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Difficulty' in body and '>8<' in body
    assert 'Grind' in body and '>6<' in body
    assert 'Fun' in body and '>9<' in body
    assert 'Hours' in body and '>62<' in body
    assert '4.5' in body


def test_the_card_leads_with_landscape_art_and_falls_back_to_the_cover(client):
    """A wide card wants a wide image, and the portrait cover is the one ratio this shape has no room for.
    The chain is the shared `Concept.landscape_url` (IGDB screenshots -> artworks -> PSN GAMEHUB art); the
    cover is the fallback, because plenty of concepts have no landscape art at all."""
    profile = ProfileFactory(is_linked=True)
    withshot = _rated(profile, title='Has a screenshot')
    IGDBMatchFactory(concept=withshot.concept, igdb_screenshot_image_ids=['sh0t1d'])
    _rated(profile, title='No landscape art')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'sh0t1d' in body
    # The art-less card still draws SOMETHING in the panel rather than a hole in the wall.
    assert body.count('pp-rcard__shot') >= 2
    assert 'pp-rcard__shot--cover' in body


def test_the_landscape_art_costs_no_extra_query(client):
    """`landscape_url` reads the `igdb_*_image_ids` columns and `bg_url`, both already on rows the page
    selects -- so it must not add a query per card, and must not drag `raw_response` in behind it."""
    profile = ProfileFactory(is_linked=True)
    for i in range(6):
        rating = _rated(profile, title=f'Game {i}')
        IGDBMatchFactory(concept=rating.concept, igdb_screenshot_image_ids=[f'shot{i}'])

    with CaptureQueriesContext(connection) as ctx:
        rows = build_profile_ratings_page(profile)
        [r.concept.landscape_url for r in rows]

    assert len(ctx.captured_queries) == 4, (
        'the builder is documented as four queries flat on the base wall -- a fifth is a decision, not '
        'an accident'
    )
    assert not any('raw_response' in q['sql'] for q in ctx.captured_queries)


def test_every_card_reserves_room_for_a_quick_take(client):
    """A grid row is as tall as its TALLEST card, so one long take inflates every card beside it and the
    ones without a take end up mostly blank. Reserving three lines on every card is what removes that --
    this file's first cut argued the opposite and produced exactly the raggedness it was trying to avoid.

    Three rather than two because the blurb is capped at 140 characters, which is about three lines at
    this width, so the clamp almost never actually bites."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Silent')                                   # no take at all
    _rated(profile, title='Chatty', blurb='Worth every hour of it.')  # a short one

    body = client.get(_url(profile), **CF).content.decode()
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    take = css[css.index('.pp-rcard__take {'):]
    take = take[:take.index('}')]

    # The box is drawn on BOTH cards -- reserving nothing on the silent one would defeat the point --
    # and the silent one SAYS so rather than sitting blank, which reads as content that failed to load.
    assert 'class="pp-rcard__take"' in body                 # the card with a take
    assert 'pp-rcard__take--empty' in body                  # the card without one
    assert 'No thoughts yet' in body
    assert 'min-height: calc(4 * 1.45em' in take, 'the take no longer reserves its lines'


def _with_dlc(profile, title='Has DLC', pack='The Old Hunters'):
    """One base-game rating and one DLC rating on the same concept."""
    concept = ConceptFactory(unified_title=title)
    ProfileGameFactory(profile=profile, game=GameFactory(concept=concept))
    dlc = ConceptTrophyGroupFactory(concept=concept, trophy_group_id='001', display_name=pack)
    UserConceptRatingFactory(profile=profile, concept=concept, concept_trophy_group=None)
    UserConceptRatingFactory(profile=profile, concept=concept, concept_trophy_group=dlc)
    return concept, dlc


def test_dlc_ratings_get_their_own_wall(client):
    """A DLC pack is rated separately from the game it belongs to, so a mixed wall shows the same title
    TWICE with two different scores and leaves the reader working out why. `concept_trophy_group` is the
    distinction -- null is the base game -- and the two sets never share a page."""
    profile = ProfileFactory(is_linked=True)
    _with_dlc(profile)

    games = build_profile_ratings_page(profile)
    dlc = build_profile_ratings_page(profile, dlc=True)

    assert len(games) == 1 and games[0].concept_trophy_group_id is None
    assert len(dlc) == 1 and dlc[0].concept_trophy_group_id is not None


def test_the_switcher_carries_both_counts_and_appears_only_when_there_is_dlc(client):
    """The counts are what stop the split confusing the summary above it: that stays WHOLE, because a
    hunter's taste does not divide at the DLC line, and the chips say how the wall beneath is divided.

    Hidden entirely for a hunter with no DLC ratings -- a switcher whose second chip is always empty is a
    control that only ever does nothing."""
    plain = ProfileFactory(is_linked=True)
    _rated(plain, title='Base only')
    assert 'set=dlc' not in client.get(_url(plain), **CF).content.decode()

    profile = ProfileFactory(is_linked=True)
    _with_dlc(profile)
    body = client.get(_url(profile), **CF).content.decode()

    assert 'set=dlc' in body and 'set=games' in body
    # The summary is unsplit: it counts everything they have rated, both sets.
    assert profile_rating_summary(profile)['count'] == 2
    assert profile_rating_summary(profile)['base_count'] == 1
    assert profile_rating_summary(profile)['dlc_count'] == 1


def test_the_set_rides_the_sort_form_so_page_two_stays_on_it(client):
    """The scroller serializes that form to fetch the next page. Without the set in it, page 2 of the DLC
    wall comes back as base games -- and appends them to the DLC wall, which is worse than not scrolling."""
    profile = ProfileFactory(is_linked=True)
    _with_dlc(profile)

    body = client.get(_url(profile, set='dlc'), **CF).content.decode()
    form = body[body.index('id="ratings-form"'):]
    form = form[:form.index('</form>')]

    assert 'name="set" value="dlc"' in form


def test_switching_view_does_not_teleport_the_scroll(client):
    """Switching tabs jumped to roughly the bottom of the wall, and the animation was not the cause.

    `InfiniteScroller`'s `scrollKey` persists `window.scrollY` to localStorage under one GLOBAL key when a
    filter form submits, and the next scroller created ANYWHERE reads it back and smooth-scrolls there.
    `create()` also bails early when its grid is absent -- an empty results wall renders no grid -- so a
    saved value could survive unconsumed until the scroller built for a different tab picked it up, at a
    position that belonged to a different and longer wall.

    The option exists for pages that full-reload on filter, where the position genuinely is lost. This one
    swaps through HTMX end to end, so there is nothing to restore and a restore can only move you."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Anything')

    body = client.get(_url(profile), **CF).content.decode()
    script = body[body.index('InfiniteScroller.create'):]
    script = script[:script.index('});')]

    assert 'scrollKey' not in script, 'the profile scroller restores a saved scroll position again'


def test_a_view_change_re_anchors_to_the_switcher(client):
    """The remaining half of the jump, and nothing scrolled anyone: the page gets SHORTER under them.
    Swap a 1200px wall in while they are 3000px down a 4000px one and the browser has nowhere to put
    them -- it clamps scrollY to the new document height, which is the bottom of the incoming wall. That
    is why it only happened past a certain depth and always landed at the bottom.

    A longer wall has the mirror problem: you keep your depth and arrive halfway down content you have
    not seen. Either way the answer to "show me the other view" is to show it from its start -- but only
    once the switcher has scrolled off the top, or someone reading the hero gets yanked for asking."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Anything')

    body = client.get(_url(profile), **CF).content.decode()
    fn = body[body.index('function keepPanelInView'):]
    fn = fn[:fn.index('\n        }')]

    assert "getBoundingClientRect().top >= 0" in fn, 'the anchor fires even when the switcher is visible'
    assert "block: 'start'" in fn, 'the anchor no longer honours the sticky nav offset'
    assert "behavior: 'auto'" in fn, 'a smooth scroll here fights the slide animation'
    # Wired into the tab-content swap, and BEFORE the slide plays.
    assert body.index('keepPanelInView();') < body.index('playPendingSlide();')


def test_switching_view_slides_the_panel_in_directionally(client):
    """The shared `slideViewIn`, the same motion every other rebuilt segmented switcher uses. BOTH
    switchers on this page feed it -- the tab strip and the Ratings tab's Games/DLC control -- because
    they swap the same panel and animating one but not the other reads as a bug in whichever missed out.

    Direction is captured on CLICK rather than derived after the swap: `hx-push-url` rewrites the URL as
    part of the swap, so reading "where were we" in afterSwap races the thing being compared against."""
    profile = ProfileFactory(is_linked=True)
    _with_dlc(profile)

    body = client.get(_url(profile), **CF).content.decode()

    # Both chip families carry the hook the direction is read from.
    assert 'data-set="dlc"' in body and 'data-tab="ratings"' in body
    assert 'slideViewIn' in body, 'the panel no longer slides on a view change'
    # Captured on click, played after the panel is rebuilt -- animating first and then mutating what is
    # inside it is what makes a slide stutter.
    assert 'pendingSlide' in body
    assert body.index('initTabContent();') < body.index('playPendingSlide();')


def test_an_empty_dlc_wall_does_not_contradict_the_chip_above_it(client):
    """"No ratings yet" under a DLC chip showing a count reads as a bug. The empty state is named for the
    set you are looking at."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Base only')

    body = client.get(_url(profile, set='dlc'), **CF).content.decode()

    assert 'No DLC ratings yet' in body


def test_the_title_holds_its_second_line_open(client):
    """The card lets a long title wrap, so a card whose title does NOT wrap has to hold that line open --
    otherwise the two sit at different heights and the wall goes ragged again. Clamped to the same two it
    reserves: a title long enough for a third line would push straight past the space reserved for it."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Nier')
    _rated(profile, title='The Legend of Heroes: Trails of Cold Steel IV -- The End of Saga')

    body = client.get(_url(profile), **CF).content.decode()
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    rule = css[css.index('.pp-rcard__title {'):]
    rule = rule[:rule.index('}')]

    assert 'Nier' in body and 'Trails of Cold Steel' in body
    reserved = re.search(r'min-height: calc\((\d+) \* ([\d.]+)em', rule)
    clamped = re.search(r'-webkit-line-clamp: (\d+)', rule)
    assert reserved and clamped, 'the title stopped reserving or stopped clamping'
    assert reserved.group(1) == clamped.group(1), (
        f'the title reserves {reserved.group(1)} lines but shows {clamped.group(1)}'
    )
    assert f'line-height: {reserved.group(2)}' in rule


def test_the_take_uses_the_plat_cards_quote_mark(client):
    """The same mark on both surfaces, so a take looks like a take wherever you meet it. It replaced a
    bordered, tinted block: with the card's edge and the stamp both carrying colour, a third framed panel
    was more chrome than the sentence inside it."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Quoted', blurb='Brilliant, and never again.')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'pp-rcard__quote' in body
    assert '&ldquo;' in body or '“' in body
    plat = (ROOT / 'templates' / 'shareables' / 'plat_card.html').read_text(encoding='utf-8')
    assert '&ldquo;' in plat, 'the plat card stopped using the mark this one was matched to'


def test_the_take_reserves_exactly_as_many_lines_as_it_shows(client):
    """The block is clamped AND height-reserved, and the two counts have to agree: reserve more than you
    clamp and every card carries dead space, clamp more than you reserve and a long take pushes the card
    taller -- which is the raggedness the reserve exists to remove.

    The clamp is sized so a MAXED-OUT take fits inside it rather than being cut mid-sentence. Getting
    there needed the quote INLINE: as a flex item it reserved its width on every line of the block rather
    than the one it sits on, which at the wall's narrowest track left room for roughly 90 of the 140
    characters a blurb can hold."""
    take = 'A brutal, beautiful grind that I would absolutely do again tomorrow, and probably will.'
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='At length', blurb=take)

    body = client.get(_url(profile), **CF).content.decode()
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    box = css[css.index('.pp-rcard__take {'):]
    box = box[:box.index('}')]
    txt = css[css.index('.pp-rcard__take-txt {'):]
    txt = txt[:txt.index('}')]
    rule = box + txt

    assert take in body
    reserved = re.search(r'min-height: calc\((\d+) \* ([\d.]+)em', box)
    clamped = re.search(r'-webkit-line-clamp: (\d+)', txt)
    assert reserved and clamped, 'the take stopped reserving or stopped clamping'
    assert reserved.group(1) == clamped.group(1), (
        f'the take reserves {reserved.group(1)} lines but shows {clamped.group(1)}'
    )
    # The reserve counts lines at the TEXT's own line-height, so the two have to be the same number.
    assert f'line-height: {reserved.group(2)}' in rule
    # And the QUOTE stays inline. As a flex item it reserved its width on every line of the block rather
    # than the one it sits on, which is what made a full take impossible to fit. (The box around it is a
    # flex container, to centre the placeholder -- that is a different thing.)
    quote = css[css.index('.pp-rcard__quote {'):]
    assert 'flex' not in quote[:quote.index('}')]


def test_the_card_carries_their_recommendation(client):
    """The card's punchline. Every other figure on it describes what the platinum was LIKE; this one says
    whether to go and do it -- and the label comes from the model's choices, so the strings live in
    exactly one place."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Rough Platinum')
    game = GameFactory(concept=concept, defined_trophies={'platinum': 1})
    # The platinum TROPHY, not just the count on the game. Whether the wording says "platinum" is a fact
    # about the concept, so it is asked of the trophies the concept defines -- the same question, asked
    # the same way, as the form that produced the label in the first place.
    TrophyFactory(game=game, trophy_type='platinum')
    ProfileGameFactory(profile=profile, game=game)
    UserConceptRatingFactory(profile=profile, concept=concept, recommendation='good_game_bad_plat')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Good game, tough plat' in body
    assert 'data-rec="good_game_bad_plat"' in body


def test_a_set_with_no_platinum_does_not_call_one_rough(client):
    """The middle option NAMES the thing that was rough, so on a DLC pack -- or a game that never defined
    a platinum -- "tough plat" names a trophy the set has not got."""
    profile = ProfileFactory(is_linked=True)
    # A game with no platinum defined at all.
    _rated(profile, title='No Plat Here', recommendation='good_game_bad_plat')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Good game, tough trophies' in body
    assert 'Good game, tough plat' not in body


def test_the_wording_follows_the_concept_not_the_copy_they_happen_to_own(client):
    """Whether a set ends in a platinum is a fact about the TITLE. Reading it off the hunter's own attached
    game got it wrong twice over: a concept they own no copy of (possible after a merge re-points a rating
    onto a survivor) has no game at all and fell through to "tough trophies" on a real platinum, and a
    hunter whose only copy is a no-platinum port got the same on a concept that plainly defines one."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Merged Away')
    # The concept defines a platinum, but this hunter owns no copy of it -- so `card_game` is None.
    TrophyFactory(game=GameFactory(concept=concept), trophy_type='platinum')
    UserConceptRatingFactory(profile=profile, concept=concept, recommendation='good_game_bad_plat')

    rows = build_profile_ratings_page(profile)

    assert rows[0].card_game is None, 'fixture no longer reproduces the unowned-concept case'
    assert rows[0].recommendation_text == 'Good game, tough plat'


def test_a_dlc_rating_never_calls_its_platinum_rough(client):
    """A DLC pack has no platinum by definition, whatever the base game has."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Has DLC')
    ProfileGameFactory(profile=profile,
                       game=GameFactory(concept=concept, defined_trophies={'platinum': 1}))
    dlc = ConceptTrophyGroupFactory(concept=concept, trophy_group_id='001', display_name='The Old Hunters')
    UserConceptRatingFactory(profile=profile, concept=concept, concept_trophy_group=dlc,
                             recommendation='good_game_bad_plat')

    body = client.get(_url(profile, set='dlc'), **CF).content.decode()

    assert 'Good game, tough trophies' in body
    assert 'Good game, tough plat' not in body


def test_the_cards_edge_and_stamp_both_carry_the_verdict(client):
    """The edge used to encode the STAR tone, which is a different fact: the stars rate the game and the
    recommendation rates the platinum, so a card could be edged red for a 2-star game the hunter still
    says to do. The verdict is the thing worth spotting down a wall, so the edge, the stamp on the art and
    the label in the body are all keyed on it and can never disagree."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Edged', recommendation='skip', overall_rating=5.0)   # loved game, skip the plat

    body = client.get(_url(profile), **CF).content.decode()
    card = body[body.index('<article class="pp-rcard"'):]

    assert 'data-rec="skip"' in card[:120], 'the card is not keyed on the verdict'
    assert 'pp-rcard__stamp' in body, 'the verdict is not stamped on the art'
    # The old key is gone rather than left alongside -- two colours claiming one edge is how they drift.
    assert 'data-tone=' not in card[:120]


def test_nothing_between_the_card_and_its_stretched_link_establishes_containment():
    """`container-type` also applies LAYOUT CONTAINMENT, which makes the element a containing block for
    absolutely-positioned descendants. On `.pp-rcard__body` that captured `.pp-rcard__link::after`, whose
    `inset: 0` then covered the body instead of the card -- so the art panel, the verdict stamp on it and
    the card's own padding all stopped being clickable, while `:focus-within` went on drawing the ring
    around the whole card. Visible affordance and real hit area disagreeing is the kind of bug that reads
    as a flaky click.

    The stat strip is a block child of the body, so it measures the same width and contains nothing that
    needs to escape it -- which is why the container belongs there instead."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    body = css[css.index('.pp-rcard__body {'):]
    body = body[:body.index('\n}')]
    assert 'container-type' not in body, (
        'the card body establishes containment again -- it is an ancestor of the stretched link'
    )

    strip = css[css.index('.pp-rcard__stats {'):]
    assert 'container-type: inline-size;' in strip[:strip.index('\n}')], (
        'the stat strip lost its container, so its @container rule has nothing to measure'
    )


def test_the_quick_rate_modal_caps_the_dialog_not_the_body():
    """A `max-height` in vh on the BODY adds to the header and overruns the dialog, whose own height is
    bounded by the UA default and whose `overflow: hidden` then clips the difference. At 375x667 that ate
    the actions row -- the submit button, the one thing that must never be the part off screen, and worst
    exactly where the form is tallest since the two-column grid only engages at 640px."""
    css = (ROOT / 'static' / 'css' / 'components' / 'game-detail.css').read_text(encoding='utf-8')

    rule = css[css.index('.gd-modal--qr .gd-modal__body {'):]
    rule = rule[:rule.index('}')]
    assert 'vh' not in rule, 'the body carries a viewport cap of its own again'
    # It has to be able to shrink below its content, or a flex item simply refuses to scroll.
    assert 'min-height: 0' in rule

    # Scoped to the dialog's own rule. A bare `'max-height: 92vh' in css` would stay green on any other
    # rule in a 1600-line file once this one is deleted.
    dialog = css[css.index('.gd-modal--qr { max-height:'):]
    assert 'max-height: 92vh' in dialog[:dialog.index('}')], 'the dialog itself is uncapped'


def test_the_submit_button_cannot_end_up_below_the_fold():
    """Capping the dialog stopped the button being CLIPPED; it did not stop it being below the scroll. Eight
    stacked fields is about 130px more than a 360x740 phone has, so on a real Galaxy S8+ you still had to
    scroll to reach it -- and shrinking controls to fit is the wrong trade on the surface where they are
    hardest to hit.

    Two independent fixes, because neither alone is enough at every height. The four scored axes pair into
    two columns at EVERY width (a 1fr column in this modal is ~149px, which a slider and its scale sit in
    comfortably), which buys back more than the overflow. And the actions stick to the bottom of the scroll
    area, so a shorter phone, a landscape keyboard or large text cannot put the button out of reach either.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'game-detail.css').read_text(encoding='utf-8')

    grid = css.index('.gd-modal--qr .gd-qr {')
    breakpoint_640 = css.index('@media (min-width: 640px)', grid)
    # `minmax(0, ...)`, not a bare `1fr`: the auto floor of a bare `1fr` is the item's min-content width,
    # which for the Hours field is its input's intrinsic width -- two of those overflow a 360px phone.
    assert 'grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)' in css[grid:breakpoint_640], (
        'the two-column pairing is back behind a breakpoint, or back on a bare 1fr that overflows on a phone'
    )

    # The LAST rule of that selector -- there are two, and the first is a one-line `margin-top: 0` whose
    # slice runs past a media query and a comment into this one, so it would find `position: sticky` in a
    # rule it never indexed.
    actions = css[css.rindex('.gd-modal--qr .gd-qr__actions {'):]
    actions = actions[:actions.index('\n}')]
    assert 'position: sticky' in actions, 'the actions row can scroll out of reach again'


def test_an_unanswered_card_falls_back_rather_than_going_edgeless(client):
    """`--rec-c` is declared on the base, not only on the states that set it: an undefined custom property
    invalidates the whole declaration it appears in rather than falling back, so a rating from before the
    field existed would have left the card with no left border at all. Cyan is the house accent, so an
    unanswered card reads neutral rather than as a fourth verdict."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    card = css[css.index('.pp-rcard {'):]
    card = card[:card.index('\n}') + 2]

    assert '--rec-c: var(--pp-primary);' in card, 'the card has no fallback edge colour'
    assert 'border-left: 3px solid var(--rec-c);' in card

    # The three answers themselves live in ONE shared file now (three identical copies of the same map had
    # accumulated). Doubled attribute so they are 0-2-0 and cannot lose a specificity tie to the
    # single-class fallback above depending on which file @import happens to reach first.
    shared = (ROOT / 'static' / 'css' / 'components' / 'recommendation.css').read_text(encoding='utf-8')
    for value, token in (('worth_it', 'success'), ('good_game_bad_plat', 'warning'), ('skip', 'error')):
        assert f'[data-rec="{value}"][data-rec]' in shared
        assert f'--rec-c: var(--pp-{token});' in shared

    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Legacy')

    body = client.get(_url(profile), **CF).content.decode()
    assert 'data-rec=""' in body            # keyed, but empty -- the base colour applies
    assert 'pp-rcard__stamp' not in body    # and nothing is stamped


def test_a_rating_that_predates_the_field_shows_no_verdict_rather_than_a_neutral_one(client):
    """An unanswered question is not an answer. The wizard is already asking it; the card must not fill
    the gap with a fourth state that looks like a considered opinion."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Legacy Rating')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Legacy Rating' in body
    assert 'pp-rcard__rec' not in body


def test_a_staff_hidden_take_does_not_surface_on_its_author_s_profile(client):
    """This queryset is not `visible_blurbs()`, so the predicate is re-applied at the card. A moderated
    take reappearing on the author's own page would be a real moderation hole."""
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='Moderated', blurb='Something unpleasant', blurb_hidden=True)

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Moderated' in body            # the card is still there
    assert 'Something unpleasant' not in body


def test_the_dlc_a_rating_is_for_is_named_on_the_card(client):
    """Without it the same title appears twice on the wall with two different scores and no way to tell
    why."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Bloodborne')
    ProfileGameFactory(profile=profile, game=GameFactory(concept=concept))
    dlc = ConceptTrophyGroupFactory(concept=concept, trophy_group_id='001', display_name='The Old Hunters')
    UserConceptRatingFactory(profile=profile, concept=concept, concept_trophy_group=dlc)

    body = client.get(_url(profile, set='dlc'), **CF).content.decode()

    assert 'The Old Hunters' in body


def test_a_private_history_answers_with_no_ratings_on_the_htmx_path(client):
    """The tabs leaked once because the HTMX path answers with the tab template directly, never rendering
    the parent's visibility check. The guard is in the view, so both paths pass through it."""
    profile = ProfileFactory(is_linked=True, psn_history_public=False)
    _rated(profile, title='Private Game')

    body = client.get(_url(profile), HTTP_HX_REQUEST='true', **CF).content.decode()

    assert 'Private Game' not in body


def test_a_scroll_append_is_cards_only_and_skips_the_summary(client):
    """The scroller's response is appended into the grid, so anything else in it is either invisible or
    (worse) a second copy of the summary landing among the cards. The aggregate behind it is skipped too:
    it describes the whole set and does not change per page."""
    profile = ProfileFactory(is_linked=True)
    for i in range(3):
        _rated(profile, title=f'Game {i}')

    response = client.get(_url(profile), HTTP_X_REQUESTED_WITH='XMLHttpRequest', **CF)
    body = response.content.decode()

    assert 'pp-rcard' in body
    assert 'pp-taste' not in body
    assert response.context['rating_summary_stats'] is None


def test_the_tab_is_offered_in_the_switcher(client):
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='A')

    body = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    assert '?tab=ratings' in body


def test_the_scroller_is_told_the_size_of_the_page_it_is_given(client):
    """The scroller gates its first fetch on the grid holding a FULL page, so a mismatch here does not
    render a wrong-sized page -- it silently disables infinite scroll."""
    from trophies.services.rating_service import RATINGS_PER_PAGE

    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='A')

    body = client.get(_url(profile), **CF).content.decode()

    assert f'paginateBy: {RATINGS_PER_PAGE}' in body


def test_an_unrated_hunter_gets_an_empty_state_not_a_bare_tab(client):
    profile = ProfileFactory(is_linked=True)

    body = client.get(_url(profile), **CF).content.decode()

    assert 'No ratings yet' in body
    assert 'pp-taste' not in body       # nothing to summarize, so no summary at all


def test_only_this_hunter_s_ratings_are_on_their_wall(client):
    profile = ProfileFactory(is_linked=True)
    other = ProfileFactory(is_linked=True)
    _rated(profile, title='Mine')
    _rated(other, title='Theirs')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Mine' in body
    assert 'Theirs' not in body


def test_the_cards_never_join_the_igdb_blob(client):
    """~30 KB of unread IGDB JSON per row, and the trigger for the May 2026 web-server OOM. The cover
    template never reads it, so it must not travel with the join."""
    profile = ProfileFactory(is_linked=True)
    for i in range(6):
        _rated(profile, title=f'Game {i}')

    with CaptureQueriesContext(connection) as ctx:
        client.get(_url(profile), **CF)

    # BOTH queries that join a concept's IGDB match: the ratings themselves (for the concept's own cover)
    # and the games behind them (for `display_image_url`).
    joined = [
        q['sql'] for q in ctx.captured_queries
        if 'igdbmatch' in q['sql'].lower()
        and ('userconceptrating' in q['sql'].lower() or 'profilegame' in q['sql'].lower())
    ]
    assert joined, 'neither the ratings nor the games joined the IGDB match -- the covers would N+1'
    assert not any('raw_response' in sql for sql in joined)


def test_the_card_stacks_on_a_phone_so_the_art_can_be_landscape():
    """The art is a LANDSCAPE screenshot. Below 768px the wall is a single column, so the card is always
    full width -- and a 92px art panel is a PORTRAIT window: `object-fit: cover` cropped the frame to
    about a third of its width, a vertical slice out of the middle. The same mistake this card was rebuilt
    to fix, pointed the other way.

    Stacked, the band is ~4.3:1 against a 343px card, so it shows the full width; and the body goes from
    ~251px to the whole card, so the title and quick take wrap less. Both halves improve from one change.

    Desktop is deliberately untouched: the split is correct once the card is >= 420px."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    card = css[css.index('.pp-rcard {'):]
    card = card[:card.index('\n}')]
    assert 'flex-direction: column' in card, 'the card is side-by-side on a phone again'

    art = css[css.index('.pp-rcard__art {'):]
    art = art[:art.index('\n')]
    assert 'width: 100%' in art and 'height: 80px' in art, 'the art is not a full-width band on a phone'

    # ...and the split is restored at md, on the SAME line that restores the panel width, so the two
    # cannot be changed apart.
    md = css[css.index('@media (min-width: 768px) { .pp-rcard {'):]
    md = md[:md.index('\n')]
    assert 'flex-direction: row' in md and 'width: 150px' in md, 'the desktop split was not restored'


def test_the_bands_overlay_and_stamp_follow_the_layout():
    """Two things that are silently wrong if only the flex direction moves.

    The overlay fades toward the BODY -- down when stacked, across when split. Left as `to right` on a top
    band it fades the card's right edge and leaves a hard seam along the bottom, the one edge that meets
    the content. And the stamp, centred, lands on the middle of the screenshot: the part worth seeing."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    overlay = css[css.index('.pp-rcard__art::after {'):]
    assert 'linear-gradient(to bottom' in overlay[:overlay.index('\n}')], 'the band fades the wrong edge'

    stamp = css[css.index('.pp-rcard__stamp {'):]
    assert 'right: 12px' in stamp[:stamp.index('\n}')], 'the stamp sits over the middle of the band'
