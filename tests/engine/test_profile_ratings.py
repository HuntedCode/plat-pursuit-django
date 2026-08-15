"""The profile Ratings tab: a hunter's taste, and what it costs to show it.

The properties pinned here are mostly the ones a page that merely renders would never reveal -- that the
query count does not follow the size of the wall, that a DLC rating is compared against the DLC's own
community and not its base game's, and that the shared summary sentence keeps working when it is pointed
at a person instead of a game.
"""
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
    """Three queries flat: the ratings, the community scores, the games. The wall is the one place an N+1
    is invisible in review and obvious in production."""
    profile = ProfileFactory(is_linked=True)
    for i in range(20):
        _rated(profile, title=f'Game {i}')

    with CaptureQueriesContext(connection) as ctx:
        rows = build_profile_ratings_page(profile)
        # Touch everything a card draws, so a lazy relation would fire here rather than in the template.
        [(r.card_game.display_image_url, r.concept.unified_title, r.community_avg) for r in rows]

    assert len(rows) == 20
    assert len(ctx.captured_queries) <= 4


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

    row = build_profile_ratings_page(profile)[0]

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

    assert len(ctx.captured_queries) <= 4
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

    # The block is rendered on BOTH cards -- reserving nothing on the silent one would defeat the point.
    # Matched on the exact class: a bare substring also catches `pp-rcard__take-txt` inside it.
    assert body.count('class="pp-rcard__take"') == 2
    assert 'min-height: calc(3 * 1.45em)' in take, 'the take no longer reserves its lines'


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


def test_the_quick_take_is_rendered_whole(client):
    """The take is capped at 140 characters at the source, so there is nothing worth truncating -- and
    clipping the one part of a rating that is not a number, on the tab that exists to show what someone
    thought, would cut exactly what the reader came for."""
    take = 'A brutal, beautiful grind that I would absolutely do again tomorrow, and probably will.'
    profile = ProfileFactory(is_linked=True)
    _rated(profile, title='At length', blurb=take)

    body = client.get(_url(profile), **CF).content.decode()
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    rule = css[css.index('.pp-rcard__take'):]
    rule = rule[:rule.index('}')]

    assert take in body
    assert 'line-clamp' not in rule, 'the quick take is being truncated again'


def test_the_card_carries_their_recommendation(client):
    """The card's punchline. Every other figure on it describes what the platinum was LIKE; this one says
    whether to go and do it -- and the label comes from the model's choices, so the strings live in
    exactly one place."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Rough Platinum')
    ProfileGameFactory(profile=profile,
                       game=GameFactory(concept=concept, defined_trophies={'platinum': 1}))
    UserConceptRatingFactory(profile=profile, concept=concept, recommendation='good_game_bad_plat')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Great game, rough platinum' in body
    assert 'data-rec="good_game_bad_plat"' in body


def test_a_set_with_no_platinum_does_not_call_one_rough(client):
    """The middle option NAMES the thing that was rough, so on a DLC pack -- or a game that never defined
    a platinum -- "rough platinum" names a trophy the set has not got. Read off the game already attached
    to the row, so the wording costs no query."""
    profile = ProfileFactory(is_linked=True)
    # A game with no platinum defined at all.
    _rated(profile, title='No Plat Here', recommendation='good_game_bad_plat')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Great game, rough trophies' in body
    assert 'Great game, rough platinum' not in body


def test_a_dlc_rating_never_calls_its_platinum_rough(client):
    """A DLC pack has no platinum by definition, whatever the base game has."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Has DLC')
    ProfileGameFactory(profile=profile,
                       game=GameFactory(concept=concept, defined_trophies={'platinum': 1}))
    dlc = ConceptTrophyGroupFactory(concept=concept, trophy_group_id='001', display_name='The Old Hunters')
    UserConceptRatingFactory(profile=profile, concept=concept, concept_trophy_group=dlc,
                             recommendation='good_game_bad_plat')

    body = client.get(_url(profile), **CF).content.decode()

    assert 'Great game, rough trophies' in body
    assert 'Great game, rough platinum' not in body


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


def test_an_unanswered_card_falls_back_rather_than_going_edgeless(client):
    """`--rec-c` is declared on the base, not only on the states that set it: an undefined custom property
    invalidates the whole declaration it appears in rather than falling back, so a rating from before the
    field existed would have left the card with no left border at all. Cyan is the house accent, so an
    unanswered card reads neutral rather than as a fourth verdict."""
    css = (ROOT / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    card = css[css.index('.pp-rcard {'):]
    card = card[:card.index('\n.pp-rcard[')]

    assert '--rec-c: var(--pp-primary);' in card, 'the card has no fallback edge colour'
    assert 'border-left: 3px solid var(--rec-c);' in card

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

    body = client.get(_url(profile), **CF).content.decode()

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
