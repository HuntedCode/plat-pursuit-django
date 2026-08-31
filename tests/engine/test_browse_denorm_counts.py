"""The materialized browse counts (2026-08-31): recompute_tag_covers fills
Franchise/Company.game_count+version_count and Genre/Theme.game_count+player_count+avg_rating,
replacing the per-row correlated subqueries the browse pages paid on every request. These pins
hold the DENORM to the exact semantics of the live queries it replaced.
"""
import pytest
from django.core.management import call_command

from tests.factories import (
    CompanyFactory,
    ConceptCompanyFactory,
    ConceptGenreFactory,
    GameFactory,
    GenreFactory,
    IGDBMatchFactory,
    ProfileFactory,
    ProfileGameFactory,
)
from trophies.models import ConceptFranchise, Franchise

pytestmark = pytest.mark.django_db


def _recount():
    call_command('recompute_tag_covers', verbosity=0)


def test_franchise_counts_visible_links_and_distinct_igdb():
    """game_count dedups shared IGDB ids (PS3+PS4 releases of one game = ONE game);
    version_count counts Game rows; excluded and spin-off links contribute NEITHER."""
    fr = Franchise.objects.create(igdb_id=601, name='Stack', slug='stack', source_type='franchise')
    a = GameFactory(title_platform=['PS4'])
    b = GameFactory(title_platform=['PS5'])
    IGDBMatchFactory(concept=a.concept, igdb_id=88001)
    IGDBMatchFactory(concept=b.concept, igdb_id=88001)   # same IGDB game, two versions
    ConceptFranchise.objects.create(concept=a.concept, franchise=fr)
    ConceptFranchise.objects.create(concept=b.concept, franchise=fr)
    spin = GameFactory(title_platform=['PS5'])
    ConceptFranchise.objects.create(concept=spin.concept, franchise=fr, is_spinoff=True)
    hidden = GameFactory(title_platform=['PS5'])
    ConceptFranchise.objects.create(concept=hidden.concept, franchise=fr, is_excluded=True)

    _recount()
    fr.refresh_from_db()

    assert fr.game_count == 1        # one shared IGDB id
    assert fr.version_count == 2     # two Game rows, visible links only


def test_company_counts_all_links():
    """Companies have no link visibility flags: every link's games count."""
    co = CompanyFactory()
    a = GameFactory(title_platform=['PS4'])
    b = GameFactory(title_platform=['PS5'])
    IGDBMatchFactory(concept=a.concept, igdb_id=88002)
    IGDBMatchFactory(concept=b.concept, igdb_id=88003)
    ConceptCompanyFactory(company=co, concept=a.concept, is_developer=True)
    ConceptCompanyFactory(company=co, concept=b.concept, is_publisher=True)

    _recount()
    co.refresh_from_db()

    assert co.game_count == 2
    assert co.version_count == 2


def test_tag_counts_players_and_rating():
    """game_count counts distinct member Games (the live subquery's grain); player_count counts
    distinct PROFILES, not ProfileGame rows (a hunter owning both members is ONE player);
    avg_rating averages base-game ratings only (DLC-scoped ratings excluded, matching the live
    sort's filter)."""
    from trophies.models import ConceptTrophyGroup, UserConceptRating

    genre = GenreFactory(name='Racing', slug='racing')
    a = GameFactory(title_platform=['PS4'])
    b = GameFactory(title_platform=['PS5'])
    ConceptGenreFactory(concept=a.concept, genre=genre)
    ConceptGenreFactory(concept=b.concept, genre=genre)

    one = ProfileFactory()
    ProfileGameFactory(profile=one, game=a)
    ProfileGameFactory(profile=one, game=b)          # same hunter twice -> one player
    ProfileGameFactory(profile=ProfileFactory(), game=b)

    def _rate(profile, concept, rating, ctg=None):
        return UserConceptRating.objects.create(
            profile=profile, concept=concept, concept_trophy_group=ctg, overall_rating=rating,
            difficulty=5, grindiness=5, hours_to_platinum=10, fun_ranking=5,
            recommendation='worth_it')

    _rate(one, a.concept, 8)
    _rate(one, b.concept, 4)
    # A DLC-scoped rating must NOT pull the average down (the live sort excluded it).
    ctg = ConceptTrophyGroup.objects.create(concept=b.concept, trophy_group_id='001')
    _rate(ProfileFactory(), b.concept, 1, ctg=ctg)

    _recount()
    genre.refresh_from_db()

    assert genre.game_count == 2
    assert genre.player_count == 2
    assert genre.avg_rating == 6.0


def test_stale_counts_reset_when_links_vanish():
    """A row absent from the aggregates (all links removed) RESETS to zero rather than keeping
    a stale count -- the diff walks every model row, not just the aggregated ones."""
    fr = Franchise.objects.create(
        igdb_id=602, name='Ghost', slug='ghost', source_type='franchise',
        game_count=7, version_count=9)

    _recount()
    fr.refresh_from_db()

    assert fr.game_count == 0
    assert fr.version_count == 0


# ── The request path never touches the link tables any more ───────────────────────────────────────

def _page_sql(client, url, params=None):
    from django.core.management import call_command
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    call_command('recompute_tag_covers', verbosity=0)
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url, params or {}).status_code == 200
    return [q['sql'] for q in ctx.captured_queries]


def test_franchise_browse_runs_no_link_subqueries(client):
    """THE landmine pin: the correlated per-row counts are gone -- a default Franchises browse
    request touches trophies_conceptfranchise in ZERO queries (columns carry the counts)."""
    fr = Franchise.objects.create(igdb_id=603, name='Pinned', slug='pinned', source_type='franchise')
    a, b = GameFactory(title_platform=['PS5']), GameFactory(title_platform=['PS5'])
    IGDBMatchFactory(concept=a.concept, igdb_id=88010)
    IGDBMatchFactory(concept=b.concept, igdb_id=88011)
    ConceptFranchise.objects.create(concept=a.concept, franchise=fr)
    ConceptFranchise.objects.create(concept=b.concept, franchise=fr)

    sqls = _page_sql(client, '/franchises/')

    offenders = [s for s in sqls if 'trophies_conceptfranchise' in s]
    assert not offenders, offenders[0][:160] if offenders else None


def test_company_browse_runs_no_link_subqueries(client):
    """Same pin for the worst offender: a default Companies browse request touches
    trophies_conceptcompany in ZERO queries."""
    co = CompanyFactory()
    g = GameFactory(title_platform=['PS5'])
    IGDBMatchFactory(concept=g.concept, igdb_id=88012)
    ConceptCompanyFactory(company=co, concept=g.concept, is_developer=True)

    sqls = _page_sql(client, '/companies/')

    offenders = [s for s in sqls if 'trophies_conceptcompany' in s]
    assert not offenders, offenders[0][:160] if offenders else None


def test_players_sort_never_scans_profilegame(client):
    """The genre landmine pin: the 'players' sort orders by the player_count COLUMN --
    trophies_profilegame appears in ZERO queries on that request."""
    genre = GenreFactory(name='Pinned Genre', slug='pinned-genre')
    g = GameFactory(title_platform=['PS5'])
    ConceptGenreFactory(concept=g.concept, genre=genre)
    ProfileGameFactory(profile=ProfileFactory(), game=g)

    sqls = _page_sql(client, '/genres/', {'sort': 'players'})

    offenders = [s for s in sqls if 'trophies_profilegame' in s]
    assert not offenders, offenders[0][:160] if offenders else None
