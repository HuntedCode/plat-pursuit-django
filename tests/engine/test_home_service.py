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
    # The Pursuer Card identity signature (built from the same hero, no second Lab build).
    assert ctx['pursuer_card'] is not None
    assert ctx['pursuer_card']['rank']['key'] == 'newbie'
    assert ctx['pursuer_card']['showcase'] == {'rarest': [], 'recent': []}
    # Glances: nothing pending, no in-progress badges, but the (zero-query) snapshot is present.
    assert ctx['glances']['claimable']['count'] == 0
    assert ctx['glances']['claimable']['total_xp'] == 0
    assert ctx['glances']['almost_badges'] == []
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


def test_launchers_resolve_and_carry_in_hand_stats():
    profile = ProfileFactory()

    launchers = home_service.build_home_context(profile)['launchers']

    by_label = {l['label']: l for l in launchers}
    # All five functional-page launchers resolve (validates the url names).
    assert set(by_label) == {'Career', 'Collection', 'Contracts', 'Milestones', 'Titles'}
    assert all(l['url'] and l['icon'] and l['desc'] for l in launchers)
    # Quick-stats reuse data already in hand -- Career shows your strongest job (name + level)...
    assert 'Lv' in by_label['Career']['stat']
    # ...and with nothing claimable, Contracts carries no stat.
    assert by_label['Contracts']['stat'] is None


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
    assert ctx['pursuer_card'] is None                # the card builds off the (broken) Lab hero
    assert ctx['glances']['snapshot'] is not None     # other zones still build
    assert [l['label'] for l in ctx['launchers']]      # launchers still resolve
    assert ctx['launchers'][0]['stat'] != 'Level None'  # missing level -> no bogus stat


def test_career_launcher_stat_shows_strongest_job():
    """The Career navigator tile surfaces your strongest job (the build is flattened and
    sorted by level desc), so a boosted job leads the tile's live stat."""
    from trophies.models import Job, ProfileJobXP
    from trophies.util_modules.leveling import xp_for_level
    profile = ProfileFactory()
    ProfileJobXP.objects.create(
        profile=profile, job=Job.objects.get(slug='mage'), total_xp=xp_for_level(20), level=20)

    launchers = home_service.build_home_context(profile)['launchers']
    career = next(l for l in launchers if l['label'] == 'Career')

    assert career['stat'] == 'Mage · Lv 20'


def test_milestones_launcher_shows_earned_over_active_total():
    """The Milestones tile shows earned/total, counting only ACTIVE milestones on both sides
    (earned records for retired milestones survive but must not inflate the count past total)."""
    from trophies.models import Milestone, UserMilestone
    Milestone.objects.all().delete()   # isolate from any migration-seeded catalog
    profile = ProfileFactory()
    active_earned = Milestone.objects.create(name='A', criteria_type='plat_count', criteria_details={'target': 1})
    Milestone.objects.create(name='B', criteria_type='plat_count', criteria_details={'target': 2})
    retired = Milestone.objects.create(name='C', criteria_type='plat_count', criteria_details={'target': 3}, is_active=False)
    UserMilestone.objects.create(profile=profile, milestone=active_earned)   # active + earned -> counts
    UserMilestone.objects.create(profile=profile, milestone=retired)         # retired -> excluded both sides

    launchers = home_service.build_home_context(profile)['launchers']
    milestones = next(l for l in launchers if l['label'] == 'Milestones')

    assert milestones['stat'] == '1/2 earned'
