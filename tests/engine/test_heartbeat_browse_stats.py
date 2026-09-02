"""The browse-header stats that ride the hourly site heartbeat (2026-08 consolidation): the
Trophy Lists catalogue block and the Genres & Themes index counts. Both were request-path
computes before -- the lists block a lazy cache.get_or_set (its with-a-platinum EXISTS semi-join
belongs on the cron), the tag counts two hot DISTINCT joins on every request AND swap of
/genres/. The heartbeat is now their only compute site; the views are pure cache reads.
"""
import pytest

from core.services.site_heartbeat import compute_site_heartbeat
from tests.factories import (
    ConceptFactory,
    ConceptGenreFactory,
    GameFactory,
    GenreFactory,
    TrophyFactory,
)
from trophies.models import Theme

pytestmark = pytest.mark.django_db


def test_lists_catalogue_block():
    """The four Trophy Lists scard values: np-floored totals, regional requiring BOTH the flag
    AND a region (check_and_mark_regional can set the flag before region detection lands),
    platinum coverage, and the 7-day delta riding lists_total."""
    plat = GameFactory(title_platform=['PS5'])
    TrophyFactory(game=plat, trophy_type='platinum')
    GameFactory(title_platform=['PS5'], is_regional=True, region=['JP'])
    GameFactory(title_platform=['PS5'], is_regional=True)          # flag only -> NOT regional
    GameFactory(title_platform=['PS5'], np_communication_id=None)  # below the np floor

    exp = compute_site_heartbeat()['expanded']

    assert exp['lists_total']['value'] == 3
    assert exp['lists_total']['delta'] == 3        # all created just now -> within 7 days
    assert exp['lists_regional']['value'] == 1
    assert exp['lists_with_plat']['value'] == 1


def test_genre_theme_index_counts():
    """genres/themes_with_games: only tags that actually carry at least one Game count."""
    tagged = GenreFactory(name='Racing', slug='racing')
    ConceptGenreFactory(concept=GameFactory(title_platform=['PS5']).concept, genre=tagged)
    GenreFactory(name='Empty Genre', slug='empty-genre')                    # no games -> not counted
    Theme.objects.create(igdb_id=901, name='Gameless Theme', slug='gameless-theme')      # no games -> not counted
    orphan = Theme.objects.create(igdb_id=902, name='Conceptless', slug='conceptless')
    orphan.theme_concepts.create(concept=ConceptFactory())                  # concept but no Game rows

    exp = compute_site_heartbeat()['expanded']

    assert exp['genres_with_games']['value'] == 1
    assert exp['themes_with_games']['value'] == 0


def test_a_failed_stat_flags_partial_not_fatal(monkeypatch):
    """The heartbeat's resilience contract holds for the new blocks: a failing query Nones the
    block and flips meta.is_partial instead of sinking the whole cron payload."""
    from trophies import models as trophy_models

    class _Boom:
        def __get__(self, obj, objtype=None):
            raise RuntimeError('genre table on fire')

    monkeypatch.setattr(trophy_models.Genre, 'objects', _Boom())

    data = compute_site_heartbeat()

    assert data['meta']['is_partial'] is True
    assert data['expanded']['genres_with_games']['value'] is None
    assert data['expanded']['themes_with_games']['value'] is None
    # The neighbor block is untouched by the failure.
    assert data['expanded']['lists_total']['value'] == 0


def test_jobs_block():
    """jobs_total counts the FULL board, fallback included -- the /jobs/ wall renders every job
    (Freelancer among them) and its own tally counts 25; an exclusion here contradicted the page
    (the audit's 24-vs-25 catch). Pinned by DELTA against the migration-seeded catalog: adding a
    fallback job MOVES the count."""
    from tests.factories import ProfileFactory
    from trophies.models import Contract, Job, ProfileJobXP

    seeded = Job.objects.count()
    assert seeded > 0, 'the migration-seeded Job catalog must be present'
    job = Job.objects.exclude(is_fallback=True).first()
    Contract.objects.create(name='Live One', slug='live-one', igdb_id=70001, is_live=True)
    Contract.objects.create(name='Dead One', slug='dead-one', igdb_id=70002, is_live=False)
    ProfileJobXP.objects.create(profile=ProfileFactory(), job=job, total_xp=450)
    ProfileJobXP.objects.create(profile=ProfileFactory(), job=job, total_xp=250)

    exp = compute_site_heartbeat()['expanded']
    assert exp['jobs_total']['value'] == seeded
    assert exp['contracts_live']['value'] == 1
    assert exp['job_xp_banked']['value'] == 700

    Job.objects.create(name='Fallback', slug='fallback-x', discipline=job.discipline,
                       is_fallback=True)
    assert compute_site_heartbeat()['expanded']['jobs_total']['value'] == seeded + 1


def test_franchise_and_company_blocks():
    """Franchise/series split by source_type; games counts are Game rows whose concept carries a
    (visible) link -- an excluded franchise link contributes neither its game nor its spin-off
    flag; developer/publisher counts are companies with at least one flagged link."""
    from tests.factories import CompanyFactory, ConceptCompanyFactory
    from trophies.models import ConceptFranchise, Franchise

    fr = Franchise.objects.create(igdb_id=501, name='Souls', slug='souls', source_type='franchise')
    Franchise.objects.create(igdb_id=501, name='Souls Series', slug='souls-series',
                             source_type='collection')
    linked = GameFactory(title_platform=['PS5'])
    ConceptFranchise.objects.create(concept=linked.concept, franchise=fr)
    spin = GameFactory(title_platform=['PS5'])
    ConceptFranchise.objects.create(concept=spin.concept, franchise=fr, is_spinoff=True)
    # A second spin-off link on the SAME concept (the multi-collection case): spin-offs count
    # distinct CONCEPTS, not links.
    series = Franchise.objects.create(igdb_id=502, name='Souls Coll', slug='souls-coll',
                                      source_type='collection')
    ConceptFranchise.objects.create(concept=spin.concept, franchise=series, is_spinoff=True)
    hidden = GameFactory(title_platform=['PS5'])
    ConceptFranchise.objects.create(concept=hidden.concept, franchise=fr,
                                    is_excluded=True, is_spinoff=True)

    dev = CompanyFactory()
    ConceptCompanyFactory(company=dev, concept=linked.concept, is_developer=True,
                          is_publisher=False)
    pub_only = CompanyFactory()
    ConceptCompanyFactory(company=pub_only, concept=spin.concept, is_developer=False,
                          is_publisher=True)
    CompanyFactory()   # linkless -> counted in companies_total only

    exp = compute_site_heartbeat()['expanded']
    assert exp['franchises_total']['value'] == 1
    assert exp['series_total']['value'] == 2
    assert exp['franchise_games']['value'] == 2        # excluded link's game not counted
    assert exp['franchise_spinoffs']['value'] == 1     # distinct concepts; excluded link not counted
    assert exp['companies_total']['value'] == 3
    assert exp['companies_developers']['value'] == 1
    assert exp['companies_publishers']['value'] == 1
    assert exp['company_games']['value'] == 2


def test_tag_coverage_pair():
    """games_tagged counts a game once no matter how many tags it carries; tags_applied counts
    every link."""
    from trophies.models import ConceptTheme, Theme

    game = GameFactory(title_platform=['PS5'])
    g1 = GenreFactory(name='Action', slug='action')
    g2 = GenreFactory(name='RPG', slug='rpg')
    ConceptGenreFactory(concept=game.concept, genre=g1)
    ConceptGenreFactory(concept=game.concept, genre=g2)
    theme = Theme.objects.create(igdb_id=903, name='Open world', slug='open-world')
    ConceptTheme.objects.create(concept=game.concept, theme=theme)
    GameFactory(title_platform=['PS5'])   # untagged -> not counted

    exp = compute_site_heartbeat()['expanded']
    assert exp['games_tagged']['value'] == 1
    assert exp['tags_applied']['value'] == 3


# ── The four page renders (Jeffrey's four-stat-grid ask, 2026-08-31) ──────────────────────────────

def _warm(**expanded):
    """Write the hourly heartbeat bucket (the badge-catalog header tests' pattern)."""
    from django.core.cache import cache
    from django.utils import timezone

    now = timezone.now()
    key = f"site_heartbeat_{now.date().isoformat()}_{now.hour:02d}"
    cache.set(key, expanded, 120)
    return key


def _page_pins(client, url, payload, marker, context_key, swap_headers):
    """The uniform contract: warm heartbeat -> grid renders + context dict; cold -> gated off;
    swap -> the stats key is None/absent (header furniture never rides a partial)."""
    from django.core.cache import cache

    key = _warm(**payload)
    try:
        warm = client.get(url)
    finally:
        cache.delete(key)
    assert warm.status_code == 200
    assert marker in warm.content.decode()
    assert warm.context[context_key] is not None

    cold = client.get(url)
    assert cold.status_code == 200
    assert marker not in cold.content.decode()
    assert cold.context.get(context_key) is None

    key = _warm(**payload)
    try:
        swap = client.get(url, **swap_headers)
    finally:
        cache.delete(key)
    assert not swap.context.get(context_key)


def test_jobs_browse_grid(client):
    _page_pins(
        client, '/jobs/',
        {'expanded': {
            'jobs_total': {'value': 25}, 'contracts_live': {'value': 12},
            'games_in_contracts': {'value': 800}, 'job_xp_banked': {'value': 91000},
        }},
        'Job XP banked', 'jobs_stats', {'HTTP_HX_REQUEST': 'true'},
    )


def test_hunters_browse_grid(client):
    _page_pins(
        client, '/hunters/',
        {'always': {
            'profiles_total': {'value': 5100}, 'trophies_total': {'value': 9000000},
            'trophies_24h': {'value': 4200},
         },
         'expanded': {'platinums_total': {'value': 61000}}},
        'Hunters tracked', 'hunters_stats',
        {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'},
    )


def test_franchise_browse_grid(client):
    _page_pins(
        client, '/franchises/',
        {'expanded': {
            'franchises_total': {'value': 900}, 'series_total': {'value': 400},
            'franchise_games': {'value': 7000}, 'franchise_spinoffs': {'value': 120},
        }},
        'Spin-offs', 'franchise_stats', {'HTTP_HX_REQUEST': 'true'},
    )


def test_company_browse_grid(client):
    _page_pins(
        client, '/companies/',
        {'expanded': {
            'companies_total': {'value': 3100}, 'companies_developers': {'value': 2300},
            'companies_publishers': {'value': 1400}, 'company_games': {'value': 15000},
        }},
        # NOT 'Publishers': the page's own H1 is "Developers & Publishers", which made the
        # cold-state absence assertion impossible -- the marker must be unique to the grid.
        'with a known company', 'company_stats', {'HTTP_HX_REQUEST': 'true'},
    )


def test_recently_added_heartbeat_pair(client):
    """RA's grid = 2 LIVE window counts + the heartbeat pair, individually gated: cold cron
    keeps the live pair and drops only the stale pair."""
    from django.core.cache import cache
    from django.urls import reverse

    GameFactory(title_platform=['PS5'])
    key = _warm(always={'games_total': {'value': 61000, 'delta': 210}})
    try:
        warm = client.get(reverse('recently_added')).content.decode()
    finally:
        cache.delete(key)
    assert 'Catalogue' in warm and '61,000' in warm and '>210<' in warm

    cold = client.get(reverse('recently_added')).content.decode()
    assert 'New games' in cold                     # the live pair survives a cold cron
    assert 'Catalogue' not in cold and 'New this week' not in cold


def test_zeroed_community_stats_never_render(client):
    """The truthy gates (the audit's zero-lie catch): a failed community compute caches ZEROS
    through _community_value(default=0) -- Hunters and RA's heartbeat pair must treat a zeroed
    payload as cold, not render 'Hunters tracked 0' / 'Catalogue 0' for two hours."""
    from django.core.cache import cache
    from django.urls import reverse

    key = _warm(always={
        'profiles_total': {'value': 0}, 'trophies_total': {'value': 0},
        'trophies_24h': {'value': 0}, 'games_total': {'value': 0, 'delta': 0},
    }, expanded={'platinums_total': {'value': 0}})
    try:
        hunters = client.get('/hunters/')
        ra = client.get(reverse('recently_added'))
    finally:
        cache.delete(key)

    assert hunters.context.get('hunters_stats') is None
    assert 'Hunters tracked' not in hunters.content.decode()
    assert ra.context['ra_catalog_total'] is None and ra.context['ra_new_this_week'] is None
    assert 'Catalogue' not in ra.content.decode()
