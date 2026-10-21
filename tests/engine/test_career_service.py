"""Tests for career_service.build_career_context (Career -- the Pursuer's job identity).

Pins the page contract: totals sourced from real ProfileJobXP (Career Level = sum of every
floored job level; Total XP = sum of job XP), the compact Total-XP label, and the disciplines
composition ring (one arc per discipline, shares summing to the whole, cumulative offsets). All
bounded by the ~25-row Job catalog.
"""
import pytest

from trophies.models import Job, ProfileJobXP, Title, UserTitle
from trophies.services import job_render
from trophies.services.career_service import _compact, build_career_context
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("value, label", [
    (0, '0'), (999, '999'), (1000, '1.0K'), (12300, '12.3K'),
    (999_999, '1000.0K'), (1_000_000, '1.0M'), (2_600_000, '2.6M'),
])
def test_compact_label_boundaries(value, label):
    assert _compact(value) == label


def _family_job_count():
    """Jobs that land in one of the 5 canonical families (what the Lab actually counts)."""
    return Job.objects.filter(discipline__in=job_render.DISCIPLINE_LABELS.keys()).count()


def test_empty_profile_floors_every_element_to_level_one():
    profile = ProfileFactory()

    ctx = build_career_context(profile)
    lab = ctx['career']

    n = _family_job_count()
    assert lab['total_xp'] == 0
    assert lab['total'] == n
    assert lab['highest_level'] == 1        # every element floored to level 1
    assert lab['total_level'] == 1          # ...and the Pursuer Level, which counts levels GAINED, is 1
    assert ctx['hero']['pursuer_level'] == 1 and ctx['hero']['pursuer_rank']['key'] == 'newbie'
    assert ctx['total_xp_compact'] == '0'


def test_totals_reflect_profile_job_xp():
    profile = ProfileFactory()
    job = Job.objects.get(slug='gunslinger')
    ProfileJobXP.objects.create(profile=profile, job=job, total_xp=12300, level=5)

    ctx = build_career_context(profile)
    lab = ctx['career']

    assert lab['total_xp'] == 12300                     # only this element has XP
    assert lab['total_level'] == 1 + 4                  # one element gained four levels, 1 -> 5
    assert lab['highest_level'] == 5
    assert ctx['total_xp_compact'] == '12.3K'           # compact label for the stat card


def test_discipline_headers_carry_icon_played_and_xp():
    profile = ProfileFactory()
    # Two combat jobs touched, the rest untouched.
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='gunslinger'), total_xp=12300, level=5)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='slayer'), total_xp=500, level=2)

    disciplines = build_career_context(profile)['career']['disciplines']
    by_slug = {d['slug']: d for d in disciplines}

    combat = by_slug['combat']
    assert combat['icon'] == 'swords'                       # discipline header glyph
    assert combat['played'] == 2                            # gunslinger + slayer touched
    assert combat['xp_total'] == 12800                      # 12300 + 500, summed across the discipline
    # The touched combat jobs are flagged started; the rest dormant.
    assert {j['slug'] for j in combat['jobs'] if j['started']} == {'gunslinger', 'slayer'}

    # Untouched discipline: nothing played, no XP, every job dormant.
    heart = by_slug['heart']
    assert heart['played'] == 0
    assert heart['xp_total'] == 0
    assert all(not j['started'] for j in heart['jobs'])


def test_hero_dominant_discipline_tints_only_when_earned():
    # Fresh Pursuer (all jobs level 1, no XP) -> no dominant discipline, so the hero stays neutral.
    assert build_career_context(ProfileFactory())['hero']['dominant_disc'] is None

    profile = ProfileFactory()
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='slayer'), total_xp=9000, level=10)
    # Slayer is a combat job, so combat leads by average -> it drives the ambient tint.
    assert build_career_context(profile)['hero']['dominant_disc'] == 'combat'


def test_hero_mirrors_lab_totals():
    profile = ProfileFactory(display_psn_username='Pursuer1')
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=4000, level=4)

    ctx = build_career_context(profile)
    hero, lab = ctx['hero'], ctx['career']

    assert hero['pursuer_name'] == 'Pursuer1'
    assert hero['pursuer_level'] == lab['total_level']
    assert hero['total_job_xp'] == lab['total_xp']
    assert hero['job_count'] == lab['total']
    assert hero['active_title'] is None     # no displayed UserTitle


def test_hero_carries_pursuer_rank():
    """The hero exposes the account-wide Pursuer rank (off the Pursuer Level) for the DNA ring label.
    A fresh profile has gained no levels, so it is Pursuer Level 1, which sits at Newbie."""
    profile = ProfileFactory()

    rank = build_career_context(profile)['hero']['pursuer_rank']

    assert rank['key'] == 'newbie'          # level 1 is under the Recruit floor (10)
    assert rank['label'] == 'Newbie' and rank['division'] is None


def test_dna_ring_arcs_sum_to_the_whole():
    profile = ProfileFactory()
    # Spread XP/levels across two different families so the ring has distinct shares.
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='gunslinger'), total_xp=9000, level=8)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=3000, level=3)

    hero = build_career_context(profile)['hero']
    ring = hero['ring']

    assert len(ring) == len(job_render.DISCIPLINE_LABELS)   # one arc per family
    # Dash segments are each family's share of the total level; together they fill the circle.
    # MOVED TO `job_render`, from `career_service._RING_C`. The ring's arc geometry became shared the moment
    # a third host needed it (the Hall of Fame plaque), and a private constant here forced that host to
    # choose between importing a private name and copying the number -- which is the drift the ring partial's
    # own docstring exists to forbid.
    from trophies.services.job_render import RING_CIRCUMFERENCE
    assert abs(sum(seg['dash'] for seg in ring) - RING_CIRCUMFERENCE) < 0.5
    assert abs(sum(seg['share_pct'] for seg in ring) - 100) <= 2   # 5-way rounding tolerance
    # Offsets are the running cumulative start of each arc (first starts at 0); each arc
    # begins where the previous ones ended (negative stroke-dashoffset convention).
    assert ring[0]['offset'] == 0.0
    assert ring[1]['offset'] == round(-ring[0]['dash'], 2)
    assert all(seg['offset'] <= 0 for seg in ring)


def test_the_ring_counts_the_levels_gained_that_its_centre_counts():
    """The arcs (and Career's legend, which prints each arc's `total`) count levels GAINED, the quantity
    the Pursuer Level in the ring's centre counts, so the five add up to that level less the starting 1.
    Under the pre-2026-10 rule a newcomer's legend read "5" in every discipline beside a centre of 25
    (DL-04 in docs/design/reviews/daily-loop.md)."""
    profile = ProfileFactory()
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='gunslinger'), total_xp=21000, level=8)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=6000, level=3)

    hero = build_career_context(profile)['hero']

    totals = {seg['slug']: seg['total'] for seg in hero['ring']}
    assert sum(totals.values()) == hero['pursuer_level'] - 1 == 7 + 2
    assert sorted(totals.values()) == [0, 0, 0, 2, 7]

    newcomer = build_career_context(ProfileFactory())['hero']
    assert newcomer['pursuer_level'] == 1 and {seg['total'] for seg in newcomer['ring']} == {0}


def test_empty_profile_ring_splits_into_equal_family_arcs():
    """A fresh profile has gained nothing in any family, which the ring draws as 5 equal arcs at 20%
    each rather than as five zero-length ones (a ring of nothing reads as broken, not as new)."""
    profile = ProfileFactory()

    ring = build_career_context(profile)['hero']['ring']

    assert len(ring) == 5
    assert all(seg['share_pct'] == 20 for seg in ring)


def test_active_title_surfaces_displayed_user_title():
    profile = ProfileFactory()
    title = Title.objects.create(name='Platinum Sovereign')
    UserTitle.objects.create(profile=profile, title=title, source_type='badge', is_displayed=True)

    hero = build_career_context(profile)['hero']

    assert hero['active_title'] == 'Platinum Sovereign'


def test_broken_lab_zone_degrades_without_500(monkeypatch):
    """A failure in the element build leaves lab=None and degrades the hero to zeros and the starting
    level (still rendered) rather than raising a 500. Level 1, not 0: no Pursuer is below 1, and at 0
    the rank ladder showed Newbie as not yet reached."""
    monkeypatch.setattr(
        'trophies.services.career_service.job_render.build_profile_jobs',
        lambda profile: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    profile = ProfileFactory()

    ctx = build_career_context(profile)

    assert ctx['career'] is None
    assert ctx['total_xp_compact'] == '0'
    assert ctx['hero']['pursuer_level'] == 1   # hero still builds, at the starting level
    assert ctx['hero']['rank_ladder']['rungs'][0]['reached']
    assert ctx['hero']['ring'] == []


def test_element_tile_carries_prestige_tier():
    """Cap-less: an element's tile exposes its prestige tier (replacing the old cap-based
    'mastered' state), and is always progressing toward the next level."""
    from trophies.util_modules.leveling import xp_for_level
    profile = ProfileFactory()
    job = Job.objects.get(slug='mage')
    ProfileJobXP.objects.create(profile=profile, job=job, total_xp=xp_for_level(99), level=99)

    elements = job_render.build_profile_jobs(profile)
    tile = next(t for d in elements['disciplines'] for t in d['jobs'] if t['slug'] == 'mage')

    assert tile['level'] == 99
    assert tile['tier'] == 'Master' and tile['tier_key'] == 'master'
    assert tile['next_tier'] == 'Grandmaster' and tile['levels_to_next_tier'] == 51  # 150 - 99
    assert 'state' not in tile          # the old cap-based state is gone
    assert tile['progress'] == 0        # exactly at level 99, no XP into the next
