"""SEO Lane 2 (2026-08-23): structured data + social images.

Strategy: docs/design/seo-strategy.md. AggregateRating from REAL community ratings only,
ItemList on the games hub, sameAs on the Organization, and the bespoke OG images (the landing's
plat-card artifact; badge detail's own medallion art instead of the 128px logo).
"""
import pytest

from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory,
    PlatformGroupFactory, ProfileFactory, UserConceptRatingFactory,
)

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def test_game_detail_emits_aggregate_rating_from_real_ratings(client):
    """Star-snippet food, but only ever from genuine data."""
    from tests.factories import ConceptTrophyGroupFactory

    from tests.factories import IGDBMatchFactory

    concept = ConceptFactory()
    match = IGDBMatchFactory(concept=concept)   # trusted -> the ratings host resolves
    game = GameFactory(concept=concept, defined_trophies={'bronze': 5})
    ConceptTrophyGroupFactory(concept=concept)   # the base group the averages are keyed on
    for i in range(3):
        UserConceptRatingFactory(profile=ProfileFactory(is_linked=True),
                                 concept=concept, overall_rating=4.0)

    # The AggregateRating claim lives on the concept Game page since the slim-down.
    head = client.get(f'/games/{match.igdb_id}/', **CF).content.decode().split('</head>')[0]

    assert '"aggregateRating"' in head
    assert '"ratingValue": 4.0' in head
    assert '"ratingCount": 3' in head


def test_no_ratings_means_no_aggregate_rating_block(client):
    """Fabricated or empty rating markup is a structured-data policy violation."""
    from tests.factories import IGDBMatchFactory

    concept = ConceptFactory()
    match = IGDBMatchFactory(concept=concept)
    GameFactory(concept=concept, defined_trophies={'bronze': 5})

    # On the ratings host itself, so "no ratings -> no block" pins the real surface.
    head = client.get(f'/games/{match.igdb_id}/', **CF).content.decode().split('</head>')[0]

    assert 'aggregateRating' not in head


def test_the_games_hub_emits_an_item_list(client):
    # unified_title too: the condensed ItemList names rows by CONCEPT (IA phase 3).
    GameFactory(title_name='Listed Game', defined_trophies={'bronze': 1},
                concept__unified_title='Listed Game')

    head = client.get('/games/', **CF).content.decode().split('</head>')[0]

    assert '"@type": "ItemList"' in head
    assert 'Listed Game' in head
    assert '"url": "http://testserver/games/' in head, 'ItemList urls must be absolute'


def test_the_organization_links_its_social_graph(client):
    head = client.get('/', **CF).content.decode().split('</head>')[0]

    assert '"sameAs"' in head
    assert 'discord.gg' in head


def test_the_landing_wears_the_plat_card_as_its_social_image(client):
    head = client.get('/', **CF).content.decode().split('</head>')[0]

    assert 'plat_card_example.png' in head
    assert 'summary_large_image' in head
    assert 'og:image" content="http://testserver/static/' in head, 'the OG image must be absolute'


def test_badge_detail_wears_its_own_art_not_the_logo(client):
    series = BadgeSeriesFactory(series_slug='og-series', name='OG Series',
                                badge_image='badges/og-art.png')
    GroupBadgeFactory(series=series,
                      platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra HD'),
                      is_live=True)

    head = client.get('/badges/og-series/', **CF).content.decode().split('</head>')[0]

    assert 'og-art.png' in head, "the badge's own art never reached the social image"
    assert 'og:image" content="' in head
    # The logo remains only as the org schema's logo, never the og:image.
    import re
    og = re.search(r'property="og:image" content="([^"]+)"', head).group(1)
    assert 'logo.png' not in og


def test_dlc_only_concepts_emit_no_aggregate_rating(client):
    """No default CTG (a DLC-only shape) -> no base averages -> no block."""
    from tests.factories import ConceptTrophyGroupFactory

    concept = ConceptFactory()
    game = GameFactory(concept=concept, defined_trophies={'bronze': 5})
    ConceptTrophyGroupFactory(concept=concept, trophy_group_id='001', display_name='DLC 1')
    UserConceptRatingFactory(profile=ProfileFactory(is_linked=True), concept=concept)

    head = client.get(f'/games/{game.np_communication_id}/', **CF).content.decode().split('</head>')[0]

    assert 'aggregateRating' not in head


def test_jsonld_never_lets_a_title_break_out_of_the_script(client):
    """The json_script hardening: a title containing </script> must emit as unicode escapes,
    or everything after it in the head becomes live markup."""
    # The CONCEPT title is what feeds the condensed ItemList now -- poison the live surface.
    GameFactory(title_name='Evil</script><img src=x>Game', defined_trophies={'bronze': 1},
                concept__unified_title='Evil</script><img src=x>Game')

    head = client.get('/games/', **CF).content.decode().split('</head>')[0]

    assert '</script><img' not in head, 'a game title terminated the JSON-LD script element'
    assert '\\u003c/script' in head, 'the title must emit as unicode escapes'


def test_schema_rating_matches_the_visible_value(client):
    """4.0 + 4.5 averages to 4.25: the page shows 4.3 (floatformat HALF_UP); the schema must
    say the same, not banker's-rounded 4.2."""
    from tests.factories import ConceptTrophyGroupFactory

    from tests.factories import IGDBMatchFactory

    concept = ConceptFactory()
    match = IGDBMatchFactory(concept=concept)
    GameFactory(concept=concept, defined_trophies={'bronze': 5})
    ConceptTrophyGroupFactory(concept=concept)
    UserConceptRatingFactory(profile=ProfileFactory(is_linked=True), concept=concept, overall_rating=4.0)
    UserConceptRatingFactory(profile=ProfileFactory(is_linked=True), concept=concept, overall_rating=4.5)

    head = client.get(f'/games/{match.igdb_id}/', **CF).content.decode().split('</head>')[0]

    assert '"ratingValue": 4.3' in head, 'the schema disagrees with the visible rounding'


def test_an_artless_badge_falls_back_to_the_logo(client):
    series = BadgeSeriesFactory(series_slug='artless', name='Artless')
    GroupBadgeFactory(series=series,
                      platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra HD'),
                      is_live=True)

    head = client.get('/badges/artless/', **CF).content.decode().split('</head>')[0]

    import re
    og = re.search(r'property="og:image" content="([^"]+)"', head).group(1)
    assert og, 'og:image vanished on the artless path'


def test_an_empty_hub_page_emits_no_item_list(client):
    head = client.get('/games/', **CF).content.decode().split('</head>')[0]

    assert '"@type": "ItemList"' not in head

