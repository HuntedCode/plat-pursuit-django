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
    # Glances: nothing pending, no in-progress badges, but the (zero-query) snapshot is present.
    assert ctx['glances']['claimable_count'] == 0
    assert ctx['glances']['almost_badges'] == []
    assert ctx['glances']['snapshot'] is not None
    assert ctx['recent'] == []
    # The trophy-snapshot card's bridge to the profile resolves (reverse-guarded).
    assert ctx['profile_url']
    # Elements strip: every element is present (a fresh account floors them all to level 1).
    assert len(ctx['elements']) > 0
    assert all({'symbol', 'level', 'disc_slug', 'name', 'shape'} <= set(e) for e in ctx['elements'])


def test_sync_zone_reports_last_and_next():
    """The trophy card's sync status: a last_synced and either ready-now or a next-update time."""
    profile = ProfileFactory()

    sync = home_service.build_home_context(profile)['sync']

    assert sync is not None
    assert sync['last_synced'] is not None
    assert isinstance(sync['ready'], bool)
    assert sync['ready'] or sync['next_sync_time'] is not None


def test_launchers_resolve_and_carry_in_hand_stats():
    profile = ProfileFactory()

    launchers = home_service.build_home_context(profile)['launchers']

    by_label = {l['label']: l for l in launchers}
    # All five functional-page launchers resolve (validates the url names).
    assert set(by_label) == {'The Lab', 'Collection', 'Research Panel', 'Milestones', 'Titles'}
    assert all(l['url'] and l['icon'] and l['desc'] for l in launchers)
    # Quick-stats reuse data already in hand -- the Lab shows the Pursuer Level...
    assert by_label['The Lab']['stat'].startswith('Level ')
    # ...and with nothing claimable, the Research Panel carries no stat.
    assert by_label['Research Panel']['stat'] is None


def test_unique_series_keeps_closest_per_series():
    """Almost There shows one entry per badge series -- the nearest tier (the list arrives
    sorted by completion, so the first occurrence of a series wins)."""
    rows = [
        {'series_slug': 'a', 'pct': 90},
        {'series_slug': 'a', 'pct': 40},   # same series, further off -> dropped
        {'series_slug': 'b', 'pct': 70},
    ]
    assert [r['series_slug'] for r in home_service._unique_series(rows)] == ['a', 'b']


def test_home_templates_parse():
    """Catch syntax errors in the hand-written home templates (the launcher icon if-chain,
    the marquee cover partial) without needing a request/render."""
    from django.template.loader import get_template
    get_template('trophies/home.html')
    get_template('trophies/partials/home/_recent_cover.html')


def test_broken_hero_zone_degrades_without_500(monkeypatch):
    """A failure in the hero (Lab) build leaves hero=None but the rest of the page still
    assembles, rather than raising."""
    monkeypatch.setattr(
        'core.services.home_service.lab_service.build_lab_context',
        lambda profile: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    profile = ProfileFactory()

    ctx = home_service.build_home_context(profile)

    assert ctx['hero'] is None
    assert ctx['glances']['snapshot'] is not None     # other zones still build
    assert [l['label'] for l in ctx['launchers']]      # launchers still resolve
    assert ctx['launchers'][0]['stat'] != 'Level None'  # missing level -> no bogus stat
    assert ctx['elements'] == []                        # elements come from the same Lab build


def test_elements_strip_is_strongest_first():
    """The elements strip flattens the Lab and sorts by level descending, so a boosted
    element leads the strip."""
    from trophies.models import Job, ProfileJobXP
    from trophies.util_modules.leveling import xp_for_level
    profile = ProfileFactory()
    ProfileJobXP.objects.create(
        profile=profile, job=Job.objects.get(slug='mage'), total_xp=xp_for_level(20), level=20)

    elements = home_service.build_home_context(profile)['elements']

    assert elements[0]['name'] == 'Mage' and elements[0]['level'] == 20
