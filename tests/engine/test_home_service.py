"""Tests for home_service.build_home_context (the synced Home landing).

Pins the page contract: a Pursuer identity hero (reused from the Lab), the thin status
glances (pending-reward count, almost-there badges, trophy snapshot), a recent strip, and
launcher cards into the functional pages -- each zone isolated so one failure degrades to a
missing section rather than a 500.
"""
import pytest

from core.services import home_service
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def test_fresh_profile_builds_every_zone():
    profile = ProfileFactory()

    ctx = home_service.build_home_context(profile)

    # Hero is the Lab identity -- a fresh account floors to the Newbie rank.
    assert ctx['hero'] is not None
    assert ctx['hero']['pursuer_rank']['key'] == 'newbie'
    # The hero carries everything the lobby's Career CTA states: name, Level, rank title and the
    # discipline-ring arcs. The full Pursuer Card is gone -- identity is Career's own hero now, and the
    # card was the most expensive thing on this page for a second rendering of the same facts.
    assert 'pursuer_card' not in ctx
    for key in ('pursuer_name', 'pursuer_level', 'pursuer_rank', 'ring'):
        assert key in ctx['hero'], f'the Career CTA reads hero[{key!r}]'
    # Glances: nothing pending, no series in progress, but the (zero-query) snapshot is present.
    assert ctx['glances']['claimable']['count'] == 0
    assert ctx['glances']['claimable']['total_xp'] == 0
    assert ctx['glances']['closest_badge'] is None
    assert ctx['glances']['snapshot'] is not None
    assert ctx['recent'] == []
    # The trophy-snapshot card's bridge to the profile resolves (reverse-guarded).
    assert ctx['profile_url']
    assert ctx['community'] is None    # cold heartbeat cache in tests -> the section hides (degrades)


def test_compact_num_boundaries():
    cn = home_service._compact_num
    assert cn(0) == '0' and cn(999) == '999' and cn(1500) == '1.5K' and cn(2_600_000) == '2.6M'


def test_community_pulse_compacts_and_curates():
    """The community strip pulls a curated set from the heartbeat dict and compacts totals."""
    hb = {
        'always': {
            'trophies_24h': {'value': 12345, 'label': 'Earned in last 24h', 'sublabel': 'live'},
            'profiles_total': {'value': 45200, 'label': 'Hunters tracked', 'sublabel': '+90 this week'},
            'trophies_total': {'value': 1_234_567, 'label': 'Trophies tracked', 'sublabel': 'all-time'},
        },
        'expanded': {'platinums_total': {'value': 2_100_000, 'label': 'Platinums earned', 'sublabel': 'all-time'}},
    }
    pulse = home_service._build_community(hb)
    assert pulse[0] == {'value': '2.1M', 'label': 'Platinums earned', 'sub': 'all-time',
                        'icon': 'platinum', 'live': False}
    assert [c['value'] for c in pulse] == ['2.1M', '12.3K', '45.2K', '1.2M']
    # the 24h cell is the live pulse; nothing else is
    assert pulse[1]['icon'] == 'pulse' and pulse[1]['live'] is True
    assert all(c['live'] is False for c in pulse if c['icon'] != 'pulse')
    assert home_service._build_community(None) is None


def test_community_pulse_degrades_on_missing_heartbeat_cells():
    """A partial heartbeat renders fewer cells rather than erroring; survivors keep their tags,
    and with the 24h cell absent nothing is flagged live."""
    hb = {
        'always': {
            'profiles_total': {'value': 100, 'label': 'Hunters', 'sublabel': None},
            'trophies_total': {'value': 200, 'label': 'Trophies', 'sublabel': None},
        },
        'expanded': {},   # no platinums_total, and no trophies_24h (the live cell) either
    }
    pulse = home_service._build_community(hb)
    assert [c['icon'] for c in pulse] == ['users', 'trophy']   # only the present cells survive
    assert all(c['live'] is False for c in pulse)              # the live cell was absent


def test_sync_zone_reports_last_and_next():
    """The trophy card's sync status: a last_synced and either ready-now or a next-update time."""
    profile = ProfileFactory()

    sync = home_service.build_home_context(profile)['sync']

    assert sync is not None
    assert sync['last_synced'] is not None
    assert isinstance(sync['ready'], bool)
    assert sync['ready'] or sync['next_sync_time'] is not None


def test_closest_badge_is_the_series_nearest_completion():
    """Home's Collection CTA. It reads `SeriesBadgeStanding.progress_bp` -- the materialized furthest-along
    fraction -- rather than the legacy per-TIER table it borrowed from `dashboard_service` until 2026-08.

    A FINISHED series is excluded: "closest badge" pointing at one already earned is not a reason to click.
    """
    from trophies.models import SeriesBadgeStanding
    from trophies.services import collection_service
    from tests.factories import BadgeSeriesFactory, ProfileFactory

    profile = ProfileFactory(is_linked=True)
    BadgeSeriesFactory(series_slug='near', name='Nearly There')
    BadgeSeriesFactory(series_slug='far', name='Barely Started')
    BadgeSeriesFactory(series_slug='done', name='Already Earned')

    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug='far', xp=1, progress_bp=2500, stages_cleared=1, stages_total=4)
    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug='near', xp=1, progress_bp=7500, stages_cleared=3, stages_total=4)
    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug='done', xp=1, progress_bp=10000, stages_cleared=4, stages_total=4)

    closest = collection_service.closest_badge(profile)
    assert closest['series_slug'] == 'near', (
        f"expected the furthest-along unfinished series, got {closest['series_slug']!r}"
    )
    assert closest['series_name'] == 'Nearly There'
    assert (closest['cleared'], closest['total'], closest['pct']) == (3, 4, 75)


def test_home_surfaces_the_closest_badge_in_its_glances():
    """The WIRING, not just the provider. Asserting the provider in isolation left Home free to stop
    calling it -- stubbing the glance to None passed every test, which is the same gap the badge sync
    wiring had: the function was covered and the call site was not.
    """
    from trophies.models import SeriesBadgeStanding
    from tests.factories import BadgeSeriesFactory

    profile = ProfileFactory(is_linked=True)
    BadgeSeriesFactory(series_slug='soulsborne', name='Soulsborne')
    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug='soulsborne', xp=1, progress_bp=6000,
        stages_cleared=3, stages_total=5)

    glance = home_service.build_home_context(profile)['glances']['closest_badge']

    assert glance is not None, 'Home is not reading the closest-badge provider at all'
    assert glance['series_name'] == 'Soulsborne'
    assert (glance['cleared'], glance['total']) == (3, 5)


def test_closest_badge_is_none_when_every_series_is_finished():
    """Not an empty dict or a zero -- Home branches on falsiness to show its fallback copy."""
    from trophies.models import SeriesBadgeStanding
    from trophies.services import collection_service
    from tests.factories import BadgeSeriesFactory, ProfileFactory

    profile = ProfileFactory(is_linked=True)
    BadgeSeriesFactory(series_slug='done', name='Done')
    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug='done', xp=1, progress_bp=10000, stages_cleared=4, stages_total=4)

    assert collection_service.closest_badge(profile) is None


def test_home_templates_parse():
    """Catch syntax errors in the hand-written home templates (the launcher icon if-chain,
    the marquee cover partial) without needing a request/render."""
    from django.template.loader import get_template
    get_template('trophies/home.html')
    get_template('trophies/partials/home/_recent_cover.html')
    get_template('partials/components/_pursuer_card.html')
    get_template('partials/components/_pursuer_card_cover.html')


def test_broken_hero_zone_degrades_without_500(monkeypatch):
    """A failure in the hero (Lab) build leaves hero=None but the rest of the page still
    assembles, rather than raising."""
    monkeypatch.setattr(
        'core.services.home_service.career_service.build_career_context',
        lambda profile: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    profile = ProfileFactory()

    ctx = home_service.build_home_context(profile)

    assert ctx['hero'] is None
    assert ctx['glances']['snapshot'] is not None     # other zones still build
    assert ctx['sync'] is not None                    # including the freshness line the lobby leads on


# --- Collection CTA: recently-earned medallions (the REBUILT badge system) ---

def test_recent_medallions_come_from_the_rebuilt_badge_tables():
    """The obvious reuse -- `dashboard_service.provide_recent_badges` -- reads the LEGACY `UserBadge`
    table, which still holds rows. So a wrong source here would not fail loudly; it would quietly render
    badges from the retired system. This pins the source, not just the output."""
    import ast
    import inspect

    from core.services import home_service

    # The DOCSTRING names what this function deliberately avoids, so a bare substring check over the
    # source passes on the prose. Match the code only.
    fn = ast.parse(inspect.getsource(home_service._recent_medallions).lstrip()).body[0]
    if ast.get_docstring(fn):
        fn.body = fn.body[1:]
    code = ast.unparse(fn)

    assert 'UserGroupBadge' in code, 'the CTA is not reading the rebuilt badge system'
    assert 'UserBadge.objects' not in code, 'the CTA fell back to the legacy badge table'
    assert 'build_collection_context' not in code, (
        'a bounded slice, never a collection build -- that is O(engaged series) and needs the progress '
        'read-model, which is the whale shape the Collection page was redesigned around'
    )


def test_recent_medallions_are_bounded_and_newest_first():
    from core.services import home_service

    profile = ProfileFactory()
    assert home_service._recent_medallions(profile) == []          # day one: nothing earned yet

    sig = home_service._recent_medallions.__defaults__
    assert sig and sig[0] == 3, 'the slice is unbounded or no longer defaults to three'


def test_a_pursuer_with_no_badges_still_gets_a_context():
    """Cold start: the lobby must assemble for someone who finished their first sync minutes ago."""
    from core.services import home_service

    ctx = home_service.build_home_context(ProfileFactory())

    assert ctx['recent_badges'] == []
    assert ctx['glances']['snapshot'] is not None
