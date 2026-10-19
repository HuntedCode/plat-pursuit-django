"""Developer badge pages split the stage ladder by studio.

Owner rules (2026-10): when an edition's stages span 2+ studios, each stage sits under its FILING studio
(the badge's developer first, then its studios A-Z, then "Other studios" for stages no family studio
developed), filed by the same rule `generate_badge_stages` orders by. Stage numbers run on across the
sections. Retired stages keep their one fenced section, labelled by studio inside it. The requirements
tile grid stays flat, in the same order.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.factories import (
    BadgeSeriesFactory, CompanyFactory, ConceptBundleFactory, ConceptCompanyFactory, ConceptFactory,
    GameFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory, StageFactory,
)
from trophies.models import ProfileGame, ProfileTrophyGroup, TrophyGroup
from trophies.services import badge_detail_service
from trophies.services.badge_detail_service import OTHER_STUDIOS_LABEL, get_badge_detail
from trophies.services.badge_orchestrator import build_catalog
from trophies.services.developer_family_service import developer_family_ids, family_developer_credits

pytestmark = pytest.mark.django_db


@pytest.fixture
def rockstar():
    """A developer with two subsidiaries, plus a studio outside the family."""
    dev = CompanyFactory(name='Rockstar Games')
    return {
        'dev': dev,
        'north': CompanyFactory(name='Rockstar North', parent=dev),
        'sd': CompanyFactory(name='Rockstar San Diego', parent=dev),
        'outsider': CompanyFactory(name='Outsider Studio'),
    }


def _badge(slug, developer, *, badge_type='developer', platform_groups=None):
    series = BadgeSeriesFactory(series_slug=slug, badge_type=badge_type, developer=developer)
    for pg in platform_groups or [PlatformGroupFactory(key=f'{slug}-u', name='Ultra HD', platforms=['PS4', 'PS5'])]:
        GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    return series


def _stage(slug, number, *studios, platforms=('PS5',), obtainable=True, bundled=False):
    """A stage holding one concept with a game per platform, credited as DEVELOPED by `studios`."""
    st = StageFactory(series_slug=slug, stage_number=number, title=f'Stage {number}')
    concept = ConceptFactory()
    games = [GameFactory(concept=concept, title_platform=[p], is_obtainable=obtainable) for p in platforms]
    if bundled:
        ConceptBundleFactory(stage=st).concepts.add(concept)
    else:
        st.concepts.add(concept)
    for studio in studios:
        ConceptCompanyFactory(concept=concept, company=studio, is_developer=True)
    return st, games


def _complete(profile, game):
    """Clear a game's base trophy group (the bar that satisfies a stage)."""
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 72})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default',
                                              defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': timezone.now()})


def _group(series, profile=None, index=0):
    return get_badge_detail(series, profile, studio_sections=True).groups[index]


def _shape(sections):
    """[(studio name, [authored stage numbers])] -- what a reader sees, section by section."""
    return [(sec['name'], [s['stage'].stage_number for s in sec['stages']]) for sec in sections]


def test_stages_split_by_studio_developer_first_then_a_to_z_then_other(rockstar):
    series = _badge('rs-order', rockstar['dev'])
    _stage('rs-order', 1, rockstar['sd'])
    _stage('rs-order', 2, rockstar['outsider'])
    _stage('rs-order', 3, rockstar['north'])
    _stage('rs-order', 4, rockstar['dev'])
    _stage('rs-order', 5, rockstar['north'])

    g = _group(series)

    assert _shape(g.studio_sections) == [
        ('Rockstar Games', [4]), ('Rockstar North', [3, 5]), ('Rockstar San Diego', [1]),
        (OTHER_STUDIOS_LABEL, [2]),
    ]
    # One badge, so the numbers run on across the sections, in the order shown (the tile grid reads g.stages).
    assert [s['stage'].stage_number for s in g.stages] == [4, 3, 5, 1, 2]
    assert [s['display_number'] for s in g.stages] == [1, 2, 3, 4, 5]


def test_a_co_credited_stage_files_under_the_developer_else_the_a_to_z_first_studio(rockstar):
    series = _badge('rs-cocredit', rockstar['dev'])
    _stage('rs-cocredit', 1, rockstar['sd'], rockstar['dev'])
    _stage('rs-cocredit', 2, rockstar['sd'], rockstar['north'])

    assert _shape(_group(series).studio_sections) == [('Rockstar Games', [1]), ('Rockstar North', [2])]


def test_each_section_carries_its_company_for_the_logo_and_link(rockstar):
    series = _badge('rs-company', rockstar['dev'])
    _stage('rs-company', 1, rockstar['dev'])
    _stage('rs-company', 2, rockstar['north'])
    _stage('rs-company', 3, rockstar['outsider'])

    sections = _group(series).studio_sections

    assert [sec['company'] for sec in sections] == [rockstar['dev'], rockstar['north'], None]
    assert [sec['url'] for sec in sections] == [
        f"/companies/{rockstar['dev'].slug}/", f"/companies/{rockstar['north'].slug}/", None,
    ]


@pytest.mark.parametrize('slug', ['', 'ctg-(core-technology-group)'])
def test_a_studio_whose_slug_makes_no_url_shows_its_logo_without_a_link(client, rockstar, slug):
    # Company slugs are stored as IGDB sends them, never slugified: one the URL converter rejects must cost
    # the link, not 500 the page. The logo tile keeps its plate styling without the link.
    north = rockstar['north']
    north.slug, north.logo_image_id = slug, 'northlogo'
    north.save()
    series = _badge('rs-badslug', rockstar['dev'])
    _stage('rs-badslug', 1, rockstar['dev'])
    _stage('rs-badslug', 2, north)

    response = client.get(f'/badges/{series.series_slug}/')
    html = response.content.decode()

    assert response.status_code == 200
    assert '<h3 class="bd-studio__name">Rockstar North</h3>' in html
    assert '<span class="bd-studio__ico bd-studio__ico--logo" aria-hidden="true">' in html


def test_a_bundle_members_credits_file_the_stage(rockstar):
    series = _badge('rs-bundle', rockstar['dev'])
    _stage('rs-bundle', 1, rockstar['dev'])
    _stage('rs-bundle', 2, rockstar['north'], bundled=True)

    assert _shape(_group(series).studio_sections) == [('Rockstar Games', [1]), ('Rockstar North', [2])]


def test_old_names_file_under_the_current_studio(rockstar):
    # DMA Design was renamed Rockstar North; the developer's own former name files under the developer.
    series = _badge('rs-rename', rockstar['dev'])
    dma = CompanyFactory(name='DMA Design', changed_company=rockstar['north'])
    old_dev = CompanyFactory(name='Rockstar Old Name', changed_company=rockstar['dev'])
    _stage('rs-rename', 1, dma)
    _stage('rs-rename', 2, old_dev)

    assert _shape(_group(series).studio_sections) == [('Rockstar Games', [2]), ('Rockstar North', [1])]


def test_a_single_studio_badge_keeps_one_flat_ladder(rockstar):
    series = _badge('rs-flat', rockstar['dev'])
    _stage('rs-flat', 1, rockstar['north'])
    _stage('rs-flat', 2, rockstar['north'])

    g = _group(series)

    assert g.studio_sections is None and g.retired_studio_sections is None
    assert [s['stage'].stage_number for s in g.stages] == [1, 2]


def test_other_badge_types_never_walk_a_studio_family(rockstar, monkeypatch):
    # Zero added cost off developer badges: the family walk is never reached, even with a developer set
    # on a franchise badge, or a developer badge type with no developer.
    def boom(*args):
        raise AssertionError('the studio family was walked')
    monkeypatch.setattr(badge_detail_service, 'developer_family_ids', boom)
    franchise = _badge('rs-franchise', rockstar['dev'], badge_type='franchise')
    unset = _badge('rs-unset', None)
    for slug in ('rs-franchise', 'rs-unset'):
        _stage(slug, 1, rockstar['dev'])
        _stage(slug, 2, rockstar['north'])

    assert _group(franchise).studio_sections is None
    assert _group(unset).studio_sections is None


def test_up_next_and_cleared_counts_follow_the_order_on_screen(rockstar):
    # Authored order puts San Diego first, but the developer's section is shown first, so "Up next" is the
    # developer's stage until it is cleared.
    series = _badge('rs-next', rockstar['dev'])
    _stage('rs-next', 1, rockstar['sd'])
    _, [dev_game] = _stage('rs-next', 2, rockstar['dev'])
    profile = ProfileFactory()

    [first, second] = _group(series, profile).stages
    assert (first['stage'].stage_number, first['is_next'], second['is_next']) == (2, True, False)

    _complete(profile, dev_game)
    g = _group(series, profile)

    assert [s['is_next'] for s in g.stages] == [False, True]
    assert [(sec['name'], sec['cleared']) for sec in g.studio_sections] == [
        ('Rockstar Games', 1), ('Rockstar San Diego', 0),
    ]


def test_retired_stages_are_labelled_by_studio_and_count_toward_the_split(rockstar):
    # Every live stage is the developer's, but a retired one is a subsidiary's: the edition spans 2
    # studios, so both ladders are labelled and the subsidiary is still named.
    series = _badge('rs-retired', rockstar['dev'])
    _stage('rs-retired', 1, rockstar['dev'])
    _stage('rs-retired', 2, rockstar['sd'], obtainable=False)
    _stage('rs-retired', 3, rockstar['dev'], obtainable=False)

    g = _group(series)

    assert _shape(g.studio_sections) == [('Rockstar Games', [1])]
    assert _shape(g.retired_studio_sections) == [('Rockstar Games', [3]), ('Rockstar San Diego', [2])]


def test_an_empty_side_of_a_split_edition_is_none(rockstar):
    series = _badge('rs-no-retired', rockstar['dev'])
    _stage('rs-no-retired', 1, rockstar['dev'])
    _stage('rs-no-retired', 2, rockstar['north'])

    assert _group(series).retired_studio_sections is None


def test_only_the_badge_page_asks_for_sections(rockstar, monkeypatch):
    # The medallion peek and the collection modal reuse get_badge_detail but never render sections, so
    # they must not pay for the family walk.
    def boom(*args):
        raise AssertionError('the studio family was walked')
    monkeypatch.setattr(badge_detail_service, 'developer_family_ids', boom)
    series = _badge('rs-modal', rockstar['dev'])
    _stage('rs-modal', 1, rockstar['dev'])
    _stage('rs-modal', 2, rockstar['north'])

    assert get_badge_detail(series, None).groups[0].studio_sections is None


def test_sections_are_per_edition(rockstar):
    # A PS3-only stage is out of Ultra HD's scope, so Ultra HD is single-studio and stays flat.
    ultra = PlatformGroupFactory(key='rs-ed-u', name='Ultra HD', platforms=['PS4', 'PS5'], sort_order=1)
    legacy = PlatformGroupFactory(key='rs-ed-l', name='Legacy HD', platforms=['PS3'], sort_order=2)
    series = _badge('rs-edition', rockstar['dev'], platform_groups=[ultra, legacy])
    _stage('rs-edition', 1, rockstar['dev'], platforms=('PS3', 'PS5'))
    _stage('rs-edition', 2, rockstar['north'], platforms=('PS3',))

    detail = get_badge_detail(series, None, studio_sections=True)
    by_key = {g.platform_group.key: g for g in detail.groups}

    assert by_key['rs-ed-u'].studio_sections is None
    assert _shape(by_key['rs-ed-l'].studio_sections) == [('Rockstar Games', [1]), ('Rockstar North', [2])]


def test_filing_costs_the_same_queries_however_many_stages(rockstar):
    def queries(slug, stage_count):
        series = _badge(slug, rockstar['dev'])
        studios = [rockstar['dev'], rockstar['north'], rockstar['sd']]
        for n in range(1, stage_count + 1):
            # Every other stage is a bundle, so a lost bundle prefetch would show up as a per-stage query.
            _stage(slug, n, studios[n % 3], bundled=n % 2 == 0)
        catalog = build_catalog(list(series.group_badges.select_related('series', 'platform_group')))
        with CaptureQueriesContext(connection) as ctx:
            filed = badge_detail_service._stage_studios(series, catalog['stages'])
        assert len(filed) == stage_count
        return len(ctx)

    assert queries('rs-q-small', 2) == queries('rs-q-large', 9)


def test_credits_can_be_limited_to_the_badges_own_games(rockstar):
    # The page asks only about its stages' games, never the family's whole catalogue.
    on_badge, elsewhere = ConceptFactory(), ConceptFactory()
    ConceptCompanyFactory(concept=on_badge, company=rockstar['north'], is_developer=True)
    ConceptCompanyFactory(concept=elsewhere, company=rockstar['north'], is_developer=True)
    family = developer_family_ids(rockstar['dev'])

    assert set(family_developer_credits(family, {on_badge.id})) == {on_badge.id}
    assert set(family_developer_credits(family)) == {on_badge.id, elsewhere.id}


def test_the_page_renders_a_header_per_studio_and_a_label_per_retired_run(client, rockstar):
    rockstar['dev'].logo_image_id = 'devlogo'
    rockstar['dev'].save()
    series = _badge('rs-render', rockstar['dev'])
    _stage('rs-render', 1, rockstar['north'])
    _stage('rs-render', 2, rockstar['dev'])
    _stage('rs-render', 3, rockstar['sd'], obtainable=False)
    _stage('rs-render', 4, rockstar['outsider'])

    html = client.get(f'/badges/{series.series_slug}/').content.decode()

    dev_url, north_url = f"/companies/{rockstar['dev'].slug}/", f"/companies/{rockstar['north'].slug}/"
    # Each family studio's name links to its company page...
    assert f'<a href="{dev_url}" class="bd-studio__link">Rockstar Games<svg' in html
    assert f'<a href="{north_url}" class="bd-studio__link">Rockstar North<svg' in html
    assert html.index('bd-studio__link">Rockstar Games') < html.index('bd-studio__link">Rockstar North')
    # ...its logo tile (pointer-only) shows the IGDB logo when it has one, else the building glyph...
    assert (f'<a href="{dev_url}" class="bd-studio__ico bd-studio__ico--logo" tabindex="-1" aria-hidden="true">'
            in html)
    assert '<img src="https://images.igdb.com/igdb/image/upload/t_logo_med/devlogo.png" alt=""' in html
    assert f'<a href="{north_url}" class="bd-studio__ico" tabindex="-1" aria-hidden="true">' in html
    # ...and "Other studios" is not a company, so it links nowhere.
    assert '<h3 class="bd-studio__name">Other studios</h3>' in html
    assert ('<h4 class="bd-retired__studio"><a href="/companies/{}/" class="bd-retired__studio-link">'
            'Rockstar San Diego</a> &middot; 1 stage</h4>').format(rockstar['sd'].slug) in html
    # Anonymous: nothing has been cleared by anyone on display, so the header offers no "cleared" count.
    assert '<span class="bd-studio__meta"><b class="pp-tally">1</b> stage</span>' in html
    # The tile jump anchors still run 1..N in display order: the developer's stage is Stage 1.
    north_header = html.index('bd-studio__link">Rockstar North')
    assert html.index('id="bd2-stage-rs-render-u-1"') < north_header < html.index('id="bd2-stage-rs-render-u-2"')


def test_a_signed_in_hunter_sees_each_studios_cleared_count(client, rockstar):
    series = _badge('rs-cleared', rockstar['dev'])
    _, [dev_game] = _stage('rs-cleared', 1, rockstar['dev'])
    _stage('rs-cleared', 2, rockstar['north'])
    profile = ProfileFactory()
    _complete(profile, dev_game)
    client.force_login(profile.user)

    html = client.get(f'/badges/{series.series_slug}/').content.decode()

    # A fully cleared studio turns green; one with stages left does not.
    assert ('<span class="bd-studio__meta"><b class="pp-tally">1</b> stage &middot; '
            '<span class="is-done"><b class="pp-tally">1</b> cleared</span></span>') in html
    assert ('<span class="bd-studio__meta"><b class="pp-tally">1</b> stage &middot; '
            '<span><b class="pp-tally">0</b> cleared</span></span>') in html
