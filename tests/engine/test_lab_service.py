"""Tests for lab_service.build_lab_context (The Lab -- the Pursuer's element identity).

Pins the page contract: totals sourced from real ProfileJobXP (Pursuer Level = sum of every
floored element level; Total XP = sum of element XP), the compact Total-XP label, and the DNA
composition ring (one arc per family, shares summing to the whole, cumulative offsets). All
bounded by the ~25-row Job catalog.
"""
import pytest

from trophies.models import Job, ProfileJobXP, Title, UserTitle
from trophies.services import element_render
from trophies.services.lab_service import _compact, build_lab_context
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
    return Job.objects.filter(discipline__in=element_render.DISCIPLINE_LABELS.keys()).count()


def test_empty_profile_floors_every_element_to_level_one():
    profile = ProfileFactory()

    ctx = build_lab_context(profile)
    lab = ctx['lab']

    n = _family_job_count()
    assert lab['total_xp'] == 0
    assert lab['total'] == n
    assert lab['total_level'] == n          # every element floored to level 1
    assert lab['highest_level'] == 1
    assert ctx['total_xp_compact'] == '0'


def test_totals_reflect_profile_job_xp():
    profile = ProfileFactory()
    job = Job.objects.get(slug='gunslinger')
    ProfileJobXP.objects.create(profile=profile, job=job, total_xp=12300, level=5)

    ctx = build_lab_context(profile)
    lab = ctx['lab']

    n = _family_job_count()
    assert lab['total_xp'] == 12300                     # only this element has XP
    assert lab['total_level'] == (n - 1) + 5            # one element boosted from 1 -> 5
    assert lab['highest_level'] == 5
    assert ctx['total_xp_compact'] == '12.3K'           # compact label for the stat card


def test_hero_mirrors_lab_totals():
    profile = ProfileFactory(display_psn_username='Pursuer1')
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=4000, level=4)

    ctx = build_lab_context(profile)
    hero, lab = ctx['hero'], ctx['lab']

    assert hero['pursuer_name'] == 'Pursuer1'
    assert hero['pursuer_level'] == lab['total_level']
    assert hero['total_job_xp'] == lab['total_xp']
    assert hero['element_count'] == lab['total']
    assert hero['active_title'] is None     # no displayed UserTitle


def test_dna_ring_arcs_sum_to_the_whole():
    profile = ProfileFactory()
    # Spread XP/levels across two different families so the ring has distinct shares.
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='gunslinger'), total_xp=9000, level=8)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=3000, level=3)

    hero = build_lab_context(profile)['hero']
    ring = hero['ring']

    assert len(ring) == len(element_render.DISCIPLINE_LABELS)   # one arc per family
    # Dash segments are each family's share of the total level; together they fill the circle.
    from trophies.services.lab_service import _RING_C
    assert abs(sum(seg['dash'] for seg in ring) - _RING_C) < 0.5
    assert abs(sum(seg['share_pct'] for seg in ring) - 100) <= 2   # 5-way rounding tolerance
    # Offsets are the running cumulative start of each arc (first starts at 0); each arc
    # begins where the previous ones ended (negative stroke-dashoffset convention).
    assert ring[0]['offset'] == 0.0
    assert ring[1]['offset'] == round(-ring[0]['dash'], 2)
    assert all(seg['offset'] <= 0 for seg in ring)


def test_empty_profile_ring_splits_into_equal_family_arcs():
    """A fresh profile floors every element to level 1, so each of the 5 evenly-sized
    families holds the same share of the total level -> 5 equal arcs at 20% each."""
    profile = ProfileFactory()

    ring = build_lab_context(profile)['hero']['ring']

    assert len(ring) == 5
    assert all(seg['share_pct'] == 20 for seg in ring)


def test_active_title_surfaces_displayed_user_title():
    profile = ProfileFactory()
    title = Title.objects.create(name='Platinum Sovereign')
    UserTitle.objects.create(profile=profile, title=title, source_type='badge', is_displayed=True)

    hero = build_lab_context(profile)['hero']

    assert hero['active_title'] == 'Platinum Sovereign'


def test_broken_lab_zone_degrades_without_500(monkeypatch):
    """A failure in the element build leaves lab=None and degrades the hero to zeros
    (still rendered) rather than raising a 500."""
    monkeypatch.setattr(
        'trophies.services.lab_service.element_render.build_profile_elements',
        lambda profile: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    profile = ProfileFactory()

    ctx = build_lab_context(profile)

    assert ctx['lab'] is None
    assert ctx['total_xp_compact'] == '0'
    assert ctx['hero']['pursuer_level'] == 0   # hero still builds, zeroed
    assert ctx['hero']['ring'] == []


def test_element_tile_carries_prestige_tier():
    """Cap-less: an element's tile exposes its prestige tier (replacing the old cap-based
    'mastered' state), and is always progressing toward the next level."""
    from trophies.util_modules.leveling import xp_for_level
    profile = ProfileFactory()
    job = Job.objects.get(slug='mage')
    ProfileJobXP.objects.create(profile=profile, job=job, total_xp=xp_for_level(99), level=99)

    elements = element_render.build_profile_elements(profile)
    tile = next(t for d in elements['disciplines'] for t in d['jobs'] if t['slug'] == 'mage')

    assert tile['level'] == 99
    assert tile['tier'] == 'Master' and tile['tier_key'] == 'master'
    assert 'state' not in tile          # the old cap-based state is gone
    assert tile['progress'] == 0        # exactly at level 99, no XP into the next
