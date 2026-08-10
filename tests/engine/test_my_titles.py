"""Titles page data layer (`MyTitlesView`).

Titles come from BADGE SERIES in the new grouping system: earning any live edition grants the series'
title. These pin the three switcher views -- Yours (held + surviving one-off awards), Within reach
(unheld but started, CLOSEST first), and All (the full live vocabulary) -- plus the equipped word.
"""
import pytest

from trophies.models import (
    BadgeSeries, SeriesBadgeStanding, Title, UserTitle,
)
from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _series_with_title(name, title_name, live=True):
    """A badge series that grants `title_name`, with one edition (live by default)."""
    series = BadgeSeriesFactory(name=name, title=Title.objects.create(name=title_name))
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=live)
    return series


def _get(client, profile):
    client.force_login(profile.user)
    return client.get('/titles/')


def test_vocabulary_is_live_series_that_grant_a_title(client):
    p = ProfileFactory()
    _series_with_title('Astro', 'Bot Wrangler')
    _series_with_title('Dormant', 'Not Yet', live=False)      # no live edition -> excluded
    BadgeSeriesFactory(name='Titleless')                       # no title -> excluded

    resp = _get(client, p)

    assert resp.status_code == 200
    assert [e['name'] for e in resp.context['all_titles']] == ['Bot Wrangler']
    assert resp.context['all_count'] == 1


def test_held_title_lands_in_yours_with_source_context(client):
    p = ProfileFactory()
    series = _series_with_title('Astro', 'Bot Wrangler')
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series',
                             source_id=series.id)

    resp = _get(client, p)

    yours = resp.context['yours']
    assert [e['name'] for e in yours] == ['Bot Wrangler']
    entry = yours[0]
    assert entry['held'] is True and entry['is_special'] is False
    assert entry['series_name'] == 'Astro'                       # what earns it
    assert entry['url'].endswith(f'/{series.series_slug}/')      # links to the badge
    assert entry['frame'] is not None                            # medallion art composed
    assert resp.context['yours_count'] == 1        # what the switcher chip shows


def test_within_reach_is_unheld_with_progress_closest_first(client):
    p = ProfileFactory()
    near = _series_with_title('Near', 'Almost There')
    far = _series_with_title('Far', 'Long Way')
    _series_with_title('Untouched', 'No Progress')               # 0 progress -> not "within reach"
    SeriesBadgeStanding.objects.create(profile=p, series_slug=near.series_slug,
                                       progress_bp=8000, stages_cleared=4, stages_total=5)
    SeriesBadgeStanding.objects.create(profile=p, series_slug=far.series_slug,
                                       progress_bp=2000, stages_cleared=1, stages_total=5)

    resp = _get(client, p)

    reach = resp.context['within_reach']
    assert [e['name'] for e in reach] == ['Almost There', 'Long Way']   # closest first
    assert reach[0]['progress_pct'] == 80
    assert (reach[0]['stages_cleared'], reach[0]['stages_total']) == (4, 5)
    assert resp.context['within_reach_count'] == 2


def test_held_title_is_not_also_within_reach(client):
    p = ProfileFactory()
    series = _series_with_title('Astro', 'Bot Wrangler')
    SeriesBadgeStanding.objects.create(profile=p, series_slug=series.series_slug,
                                       progress_bp=10000, stages_cleared=5, stages_total=5)
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series',
                             source_id=series.id)

    resp = _get(client, p)

    assert resp.context['within_reach'] == []
    assert [e['name'] for e in resp.context['yours']] == ['Bot Wrangler']


def test_surviving_one_off_award_shows_in_yours_as_special(client):
    p = ProfileFactory()
    UserTitle.objects.create(profile=p, title=Title.objects.create(name='Case Hardened'),
                             source_type='milestone', source_id=None)

    resp = _get(client, p)

    yours = resp.context['yours']
    assert [e['name'] for e in yours] == ['Case Hardened']
    assert yours[0]['is_special'] is True
    assert yours[0]['series_name'] is None and yours[0]['frame'] is None
    assert resp.context['all_titles'] == []       # not part of the live vocabulary


def test_equipped_title_and_holder_counts(client):
    p, other = ProfileFactory(), ProfileFactory()
    series = _series_with_title('Astro', 'Bot Wrangler')
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series',
                             source_id=series.id, is_displayed=True)
    UserTitle.objects.create(profile=other, title=series.title, source_type='badge_series',
                             source_id=series.id)

    resp = _get(client, p)

    assert resp.context['equipped_title'].name == 'Bot Wrangler'
    assert resp.context['all_titles'][0]['holders'] == 2       # social proof
    assert resp.context['yours'][0]['is_displayed'] is True


def test_empty_state_for_a_hunter_with_nothing(client):
    p = ProfileFactory()

    resp = _get(client, p)

    assert resp.status_code == 200
    assert resp.context['yours'] == [] and resp.context['within_reach'] == []
    assert resp.context['equipped_title'] is None


def test_legacy_badge_titles_are_not_surfaced(client):
    """This page is the NEW badge system only -- legacy `source_type='badge'` grants (still written on
    every sync until the cutover) must not appear."""
    p = ProfileFactory()
    UserTitle.objects.create(profile=p, title=Title.objects.create(name='Old Guard'),
                             source_type='badge', source_id=1)

    resp = _get(client, p)

    assert resp.context['yours'] == []
    assert 'Old Guard' not in resp.content.decode()


def test_held_title_survives_its_series_going_off_live(client):
    """A series taken off-live drops out of the catalogue -- but a title you already EARNED must not
    vanish from the page whose job is showing what you hold."""
    p = ProfileFactory()
    series = _series_with_title('Dark', 'Ghost Walker', live=False)
    UserTitle.objects.create(profile=p, title=series.title, source_type='badge_series',
                             source_id=series.id)

    resp = _get(client, p)

    assert [e['name'] for e in resp.context['yours']] == ['Ghost Walker']
    assert resp.context['all_titles'] == []          # not in the live catalogue
    assert resp.context['yours'][0]['source_label'] == 'Badge title'


def test_two_series_sharing_one_title_render_once(client):
    """BadgeSeries.title has no unique constraint. One entry per SERIES would duplicate the row, inflate
    the counts, and make the equip toggle flip two rows for a single title."""
    p = ProfileFactory()
    shared = Title.objects.create(name='Shared Word')
    for name in ('AlphaSeries', 'BetaSeries'):
        series = BadgeSeriesFactory(name=name, title=shared)
        GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    UserTitle.objects.create(profile=p, title=shared, source_type='badge_series', source_id=1)

    resp = _get(client, p)

    assert [e['name'] for e in resp.context['all_titles']] == ['Shared Word']
    assert resp.context['all_count'] == 1
    assert resp.context['yours_count'] == 1


def test_sub_one_percent_progress_still_counts_as_started(client):
    """progress_pct rounds a sub-50bp standing to 0. Gating "started" on the rounded value told a hunter
    with a cleared stage they hadn't begun."""
    p = ProfileFactory()
    series = _series_with_title('Huge', 'Long Haul')
    SeriesBadgeStanding.objects.create(profile=p, series_slug=series.series_slug,
                                       progress_bp=33, stages_cleared=1, stages_total=300)

    resp = _get(client, p)

    assert [e['name'] for e in resp.context['within_reach']] == ['Long Haul']
    assert resp.context['within_reach'][0]['progress_pct'] == 0     # rounds to 0, but it HAS started


def test_medallion_state_follows_the_viewer(client):
    """An unowned badge rendered in the 'earned' state got the earned aura next to its own padlock."""
    p = ProfileFactory()
    held = _series_with_title('Owned', 'Held Title')
    started = _series_with_title('Started', 'Partway')
    _series_with_title('Untouched', 'Not Begun')
    UserTitle.objects.create(profile=p, title=held.title, source_type='badge_series', source_id=held.id)
    SeriesBadgeStanding.objects.create(profile=p, series_slug=started.series_slug,
                                       progress_bp=5000, stages_cleared=2, stages_total=4)

    states = {e['name']: e['frame']['state'] for e in _get(client, p).context['all_titles']}

    assert states == {'Held Title': 'earned', 'Partway': 'in_progress', 'Not Begun': 'unearned'}


def test_query_count_is_flat_as_the_catalogue_grows(django_assert_num_queries, client):
    """The page must not N+1 per series -- art_layers() reads series.submitted_by for user-type series,
    which was one extra query each until the prefetch covered it.

    9, not 8: the rarity grade added ONE grouped COUNT for the pursuer denominator. Both it and the
    holder count are viewer-independent aggregates over indexed columns, bounded by the badge
    catalogue rather than by anyone's trophies. If this number climbs again, the grade has started
    querying per title."""
    p = ProfileFactory()
    for i in range(6):
        _series_with_title(f'Series {i}', f'Title {i}')
    client.force_login(p.user)
    client.get('/titles/')          # warm session/auth so the count reflects the view itself

    with django_assert_num_queries(9):
        client.get('/titles/')


# ── Render ────────────────────────────────────────────────────────────────────────────────────────

def test_page_renders_the_three_views_and_the_nameplate(client):
    p = ProfileFactory()
    held = _series_with_title('Astro', 'Bot Wrangler')
    started = _series_with_title('Ratchet', 'Lombax Legend')
    UserTitle.objects.create(profile=p, title=held.title, source_type='badge_series',
                             source_id=held.id, is_displayed=True)
    SeriesBadgeStanding.objects.create(profile=p, series_slug=started.series_slug,
                                       progress_bp=6000, stages_cleared=3, stages_total=5)

    content = _get(client, p).content.decode()

    # The word you're wearing is the page's subject, on the hero scale of the shared plate.
    assert 'ttl-plate--hero' in content and 'Bot Wrangler' in content
    # ...and every other title is the SAME component at tile scale -- the page is self-similar.
    assert 'ttl-plate--tile' in content
    assert content.count('class="ttl-wall"') == 3
    # All three panels are server-rendered behind the switcher.
    assert 'data-ttl-panel="yours"' in content
    assert 'data-ttl-panel="reach"' in content
    assert 'data-ttl-panel="all"' in content
    assert 'pp-switch' in content
    # The unheld-but-started word shows its progress toward the title.
    # (The cleared count sits in its own <strong>, so match around it.)
    assert 'Lombax Legend' in content
    assert '>3</strong> of 5 stages' in content
    assert '--horizon-progress: 60%' in content
    # Every populated panel carries a note line, so the first card sits at the same vertical
    # position in all three views -- switching must not jump.
    assert content.count('class="ttl-note"') == 3
    # a11y: each tabpanel points back at its tab and carries an sr-only heading, so the h1 -> h3 jump
    # doesn't skip a level and screen-reader users can navigate the three views by heading.
    assert content.count('aria-labelledby="ttl-tab-') == 3
    for heading in ("Titles you've earned", 'Titles within reach', 'Every title'):
        assert '<h2 class="sr-only">%s</h2>' % heading in content
    # No unrendered template syntax leaked (multi-line {# #} is a known trap).
    assert '{%' not in content and '{#' not in content


def test_equipped_plate_is_marked_worn_and_others_offer_equip(client):
    p = ProfileFactory()
    worn = _series_with_title('Astro', 'Bot Wrangler')
    spare = _series_with_title('Ratchet', 'Lombax Legend')
    UserTitle.objects.create(profile=p, title=worn.title, source_type='badge_series',
                             source_id=worn.id, is_displayed=True)
    UserTitle.objects.create(profile=p, title=spare.title, source_type='badge_series',
                             source_id=spare.id)

    content = _get(client, p).content.decode()

    assert 'is-worn' in content            # the equipped plate carries the accent edge
    assert 'data-ttl-unequip' in content   # the nameplate offers "Take it off"
    # BOTH the "Wearing" marker and the "Wear this" button ship on EVERY plate in this view -- CSS picks
    # one off `.is-worn`, so equipping is a single class toggle and the old plate flips back with it.
    assert content.count('ttl-plate__worn') == 2
    assert content.count('data-ttl-equip') == 2


# ── Rarity: the site's grade, not a second scheme ─────────────────────────────────────────────────
#
# A title's rarity comes from badge_rarity.group_rarity -- the SAME function badge detail and the browse
# gallery use -- so one title cannot read two different rarities depending on which page you're on.
# Denominator: the series' PURSUERS (SeriesBadgeStanding rows), because against the whole userbase almost
# every badge reads Mythic. Numerator: TITLE holders, because that is the number printed beside the grade.

def _pursuers(series, n):
    """n profiles making progress on the series (the rarity denominator)."""
    for _ in range(n):
        SeriesBadgeStanding.objects.create(profile=ProfileFactory(), series_slug=series.series_slug,
                                           progress_bp=1000, stages_cleared=1, stages_total=5)


def _wearers(series, n):
    for _ in range(n):
        UserTitle.objects.create(profile=ProfileFactory(), title=series.title,
                                 source_type='badge_series', source_id=series.id)


@pytest.mark.parametrize('wearers, pursuers, expected', [
    (1, 100, 'mythic'),      # 1%   -> under the 5% ceiling
    (10, 100, 'rare'),       # 10%  -> under 15
    (30, 100, 'uncommon'),   # 30%  -> under 35
    (80, 100, 'common'),     # 80%
])
def test_a_title_wears_the_sites_rarity_grade(client, wearers, pursuers, expected):
    p = ProfileFactory()
    series = _series_with_title('Crash', 'Crate Crusher')
    _pursuers(series, pursuers)
    _wearers(series, wearers)

    entry = _get(client, p).context['all_titles'][0]

    assert entry['rarity_class'] == expected


def test_the_grade_counts_title_holders_not_badge_earners(client):
    """A title is granted by ANY live edition, so it is strictly easier than any single edition -- and
    the plate prints "N wearing" right beside the grade. Grading a different population from the one
    displayed is how a card ends up reading "Mythic - 44,210 wearing"."""
    p = ProfileFactory()
    series = _series_with_title('Crash', 'Crate Crusher')
    _pursuers(series, 100)
    _wearers(series, 40)

    entry = _get(client, p).context['all_titles'][0]

    assert entry['holders'] == 40
    assert entry['rarity_pct'] == 40.0, 'the percentage must describe the number shown next to it'


def test_a_title_nobody_holds_gets_no_grade(client):
    """0 earners is unearned, not an achievement -- it must not wear the prestige grade. The page shows
    the "Be the first" nudge instead, the same treatment the browse gallery gives an unearned badge."""
    p = ProfileFactory()
    series = _series_with_title('Crash', 'Crate Crusher')
    _pursuers(series, 40)

    resp = _get(client, p)

    assert resp.context['all_titles'][0]['rarity_class'] == ''
    assert 'Be the first' in resp.content.decode()


def test_a_series_with_no_pursuers_yet_gets_no_grade(client):
    """Nothing to grade against -- group_rarity returns (None, '') rather than dividing by zero."""
    p = ProfileFactory()
    _series_with_title('Crash', 'Crate Crusher')

    entry = _get(client, p).context['all_titles'][0]

    assert entry['rarity_class'] == '' and entry['rarity_pct'] is None


def test_the_grade_reaches_the_plate_through_the_shared_component(client):
    """Titles must render rarity from components/rarity_grade.html, never its own copy.

    Asserting the SHARED markup rather than a `ttl-` class is the point: this page hand-rolled its own
    grade colours until the primitive was extracted, so a page-local class reappearing here is exactly
    the regression worth catching."""
    p = ProfileFactory()
    series = _series_with_title('Crash', 'Crate Crusher')
    _pursuers(series, 100)
    _wearers(series, 1)                                  # 1% -> mythic

    content = _get(client, p).content.decode()

    assert 'class="pp-rarity"' in content, 'the shared grade component did not render'
    assert 'data-rarity="mythic"' in content
    assert '#rarity-sparkle' in content
    assert 'Mythic' in content
    assert 'pp-rarity-surface' in content, 'the plate must take its material from the shared scale'
    assert 'ttl-plate__grade' not in content, 'a page-local grade class means the copy came back'
