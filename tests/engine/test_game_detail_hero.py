"""Tests for the rebuilt game-detail hero backend logic.

Pins:
  - `_build_outlook_context` (the anonymous "Platinum Outlook"): PSN-GLOBAL platinum rarity ->
    difficulty (4 - trophy_rarity), guarded when there's no platinum or the rarity tier isn't synced,
    and never per-user work.
  - `_build_timeline_events`: the platinum floats to WHERE it was earned in the sequence (with DLC a
    base-game plat precedes the 75%/100% overall milestones); "Started Playing" is pinned first and
    "100%" is pinned last.
"""
import itertools
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from trophies.models import Contract, GameFamily, Job
from trophies.views.game_views import GameDetailView
from tests.factories import (
    CompanyFactory, ConceptCompanyFactory, ConceptFactory, EarnedTrophyFactory, GameFactory,
    IGDBMatchFactory, ProfileFactory, ProfileGameFactory, TrophyFactory,
)

_pursuit_igdb_seq = itertools.count(70001)   # distinct raw igdb ids per test contract

pytestmark = pytest.mark.django_db


# ── _build_outlook_context ────────────────────────────────────────────────

def _outlook(game):
    return GameDetailView()._build_outlook_context(game)['outlook']


def test_outlook_ultra_rare_platinum():
    game = GameFactory()
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=2.1, trophy_rarity=0)  # Ultra Rare
    out = _outlook(game)
    assert out['has_platinum'] is True
    assert out['plat_rate'] == pytest.approx(2.1)
    assert out['plat_rarity_label'] == 'Ultra Rare'
    assert out['difficulty_level'] == 4          # 4 - 0


def test_outlook_common_platinum():
    game = GameFactory()
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=48.0, trophy_rarity=3)  # Common
    out = _outlook(game)
    assert out['plat_rarity_label'] == 'Common'
    assert out['difficulty_level'] == 1          # 4 - 3


def test_outlook_no_platinum_degrades():
    game = GameFactory()
    TrophyFactory(game=game, trophy_type='gold', trophy_earn_rate=10.0, trophy_rarity=2)
    out = _outlook(game)
    assert out['has_platinum'] is False
    assert out['plat_rate'] is None
    assert out['plat_rarity_label'] is None
    assert out['difficulty_level'] is None


def test_outlook_platinum_missing_rarity_is_guarded():
    # PSN rate present but the tier isn't synced -> no label/difficulty, but still a platinum + rate.
    game = GameFactory()
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=5.0, trophy_rarity=None)
    out = _outlook(game)
    assert out['has_platinum'] is True
    assert out['plat_rate'] == pytest.approx(5.0)
    assert out['plat_rarity_label'] is None
    assert out['difficulty_level'] is None


# ── _build_timeline_events (dynamic platinum ordering) ─────────────────────

def _timeline_labels(game, profile):
    return [e['label'] for e in GameDetailView()._build_profile_context(game, profile)['timeline_events']]


def test_timeline_platinum_floats_before_unreached_milestones():
    """DLC case: a base-game platinum earned early (low overall-completion index) sorts BEFORE the
    50%/75% milestones it precedes; Started is first and 100% is last."""
    game = GameFactory()
    profile = ProfileFactory()
    now = timezone.now()
    ProfileGameFactory(profile=profile, game=game, progress=40,
                       first_played_date_time=now - timedelta(days=10))
    tro = [TrophyFactory(game=game, trophy_type='bronze') for _ in range(8)]  # 8 total -> 75% at index 6
    tro[0].trophy_type = 'platinum'
    tro[0].save()
    # 4 earned (the platinum earned 2nd -> index 1), 4 unearned -> total_trophies stays 8.
    dates = [now - timedelta(days=d) for d in (9, 8, 7, 6)]
    EarnedTrophyFactory(profile=profile, trophy=tro[1], earned=True, earned_date_time=dates[0])  # 1st
    EarnedTrophyFactory(profile=profile, trophy=tro[0], earned=True, earned_date_time=dates[1])  # 2nd = plat
    EarnedTrophyFactory(profile=profile, trophy=tro[2], earned=True, earned_date_time=dates[2])
    EarnedTrophyFactory(profile=profile, trophy=tro[3], earned=True, earned_date_time=dates[3])
    for i in range(4, 8):
        EarnedTrophyFactory(profile=profile, trophy=tro[i], earned=False, earned_date_time=None)

    labels = _timeline_labels(game, profile)
    plat_i = labels.index('Platinum Trophy')
    assert labels[0] == 'Started Playing'
    assert labels[-1] == '100% Trophy'
    assert plat_i < labels.index('50% Trophy')
    assert plat_i < labels.index('75% Trophy')


def test_timeline_platinum_stays_late_without_dlc():
    """No DLC: the platinum is the last trophy earned, so it stays after 75% and before 100%."""
    game = GameFactory()
    profile = ProfileFactory()
    now = timezone.now()
    ProfileGameFactory(profile=profile, game=game, progress=100,
                       first_played_date_time=now - timedelta(days=5))
    tro = [TrophyFactory(game=game, trophy_type='bronze') for _ in range(4)]
    tro[3].trophy_type = 'platinum'
    tro[3].save()
    for i in range(4):
        EarnedTrophyFactory(profile=profile, trophy=tro[i], earned=True,
                            earned_date_time=now - timedelta(days=4 - i))  # plat (tro[3]) earned last

    labels = _timeline_labels(game, profile)
    assert labels.index('75% Trophy') < labels.index('Platinum Trophy') < labels.index('100% Trophy')


# ── _build_group_pct (per-group completion for the trophy-panel group headers) ──

def test_group_pct_computes_earned_over_defined():
    """Each group's % is its own earned/defined, keyed by group_id (base + DLC keyed independently)."""
    pct = GameDetailView()._build_group_pct(
        {
            'default': {'defined_trophies': {'bronze': 6, 'silver': 2, 'gold': 1, 'platinum': 1}},  # 10 defined
            '001': {'defined_trophies': {'bronze': 4, 'silver': 0, 'gold': 1, 'platinum': 0}},        # 5 defined
        },
        {
            'default': {'bronze': 3, 'silver': 1, 'gold': 1, 'platinum': 0},  # 5 earned -> 50%
            '001': {'bronze': 4, 'silver': 0, 'gold': 1, 'platinum': 0},      # 5 earned -> 100%
        },
    )
    assert pct == {'default': 50, '001': 100}


def test_group_pct_missing_totals_is_zero():
    # Group defined but the profile earned nothing in it (no totals entry) -> 0%, not a KeyError.
    pct = GameDetailView()._build_group_pct({'x': {'defined_trophies': {'bronze': 4}}}, {})
    assert pct == {'x': 0}


def test_group_pct_zero_defined_does_not_divide_by_zero():
    pct = GameDetailView()._build_group_pct({'default': {'defined_trophies': {}}}, {})
    assert pct['default'] == 0


def test_group_pct_rounds_to_nearest_int():
    # 1 of 3 -> 33.33 -> 33
    pct = GameDetailView()._build_group_pct({'g': {'defined_trophies': {'bronze': 3}}}, {'g': {'bronze': 1}})
    assert pct['g'] == 33


# ── _build_pursuit_context (contract row always carries a status tag) ───────

def _game_with_contract(job_slugs=('gunslinger',)):
    """A live Contract keyed on a raw igdb id + an anchored, trusted-matched concept whose game
    carries the membership (mirrors test_contracts_service._contract)."""
    igdb_id = next(_pursuit_igdb_seq)
    contract = Contract.objects.create(name=f'c{igdb_id}', slug=f'c-{igdb_id}', is_live=True, igdb_id=igdb_id)
    contract.jobs.set(Job.objects.filter(slug__in=job_slugs))
    concept = ConceptFactory(unified_title='Pursuit Game', anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)
    game = GameFactory(concept=concept)
    return contract, game


def test_contract_state_tag_mapping_is_complete():
    """Every status the view can derive has a (label, variant) tag, so the row can always render one."""
    tags = GameDetailView._CONTRACT_STATE_TAG
    assert set(tags) == {'available', 'not_started', 'pursuing', 'claimable', 'banked'}
    assert tags['pursuing'] == ('In Progress', 'active')
    assert tags['banked'] == ('Banked', 'done')


def test_pursuit_status_anonymous_is_available():
    _c, game = _game_with_contract()
    state = GameDetailView()._build_pursuit_context(game, None)['pursuit_contract_state']
    assert state == {'status': 'available', 'label': 'Available', 'variant': 'todo'}


def test_pursuit_status_linked_without_earned_contract_is_not_started():
    _c, game = _game_with_contract()
    profile = ProfileFactory()
    state = GameDetailView()._build_pursuit_context(game, profile)['pursuit_contract_state']
    assert state == {'status': 'not_started', 'label': 'Not Started', 'variant': 'todo'}


def test_pursuit_status_with_bare_earned_contract_is_pursuing():
    from trophies.models import EarnedContract
    contract, game = _game_with_contract()
    profile = ProfileFactory()
    EarnedContract.objects.create(profile=profile, contract=contract)   # started, nothing reached/accepted
    state = GameDetailView()._build_pursuit_context(game, profile)['pursuit_contract_state']
    assert state['status'] == 'pursuing'
    assert state['variant'] == 'active'


def test_pursuit_status_reached_not_accepted_is_claimable():
    from trophies.models import EarnedContract
    contract, game = _game_with_contract()
    profile = ProfileFactory()
    # 100% reached but the XP not yet accepted -> claimable.
    EarnedContract.objects.create(profile=profile, contract=contract, full_reached_at=timezone.now())
    state = GameDetailView()._build_pursuit_context(game, profile)['pursuit_contract_state']
    assert state['status'] == 'claimable'
    assert state['variant'] == 'claim'


def test_pursuit_status_fully_accepted_is_banked():
    from trophies.models import EarnedContract
    contract, game = _game_with_contract()
    profile = ProfileFactory()
    now = timezone.now()
    # 100% reached AND accepted, no platinum tier to accept -> banked.
    EarnedContract.objects.create(profile=profile, contract=contract,
                                  has_platinum=False, full_reached_at=now, full_accepted_at=now)
    state = GameDetailView()._build_pursuit_context(game, profile)['pursuit_contract_state']
    assert state['status'] == 'banked'
    assert state['variant'] == 'done'


# ── _build_family_versions (other concepts in the same GameFamily) ──────────

def test_family_versions_empty_without_family():
    game = GameFactory(concept=ConceptFactory())
    assert GameDetailView()._build_family_versions(game) == []


def test_family_versions_lists_siblings_with_most_played_representative():
    family = GameFamily.objects.create(canonical_name='Cool Series')
    c0 = ConceptFactory(unified_title='Cool Game', family=family)
    game = GameFactory(concept=c0)
    sib = ConceptFactory(unified_title='Cool Game Remastered', family=family)
    GameFactory(concept=sib, played_count=5)
    rep = GameFactory(concept=sib, played_count=99)   # most-played -> the representative

    fv = GameDetailView()._build_family_versions(game)
    assert len(fv) == 1                       # the current concept is excluded
    assert fv[0]['concept'].pk == sib.pk
    assert fv[0]['game'].pk == rep.pk


def test_family_versions_skips_sibling_with_no_games():
    family = GameFamily.objects.create(canonical_name='Series')
    game = GameFactory(concept=ConceptFactory(family=family))
    ConceptFactory(family=family)             # sibling concept, but no games -> nothing to link to
    assert GameDetailView()._build_family_versions(game) == []


# ── _build_other_versions (grouped by shared IGDB id, across concepts) ──────

def test_other_versions_groups_separate_concepts_by_shared_igdb_id():
    """A PS4 and a PS5 edition IGDB lists as one game are separate Concepts sharing an igdb_id -- they must
    still group as 'other platforms'."""
    c_ps4 = ConceptFactory(unified_title='Game PS4')
    IGDBMatchFactory(concept=c_ps4, igdb_id=555)
    game = GameFactory(concept=c_ps4)
    c_ps5 = ConceptFactory(unified_title='Game PS5')
    IGDBMatchFactory(concept=c_ps5, igdb_id=555)      # same igdb id, DIFFERENT concept
    ps5_game = GameFactory(concept=c_ps5)

    versions = GameDetailView()._build_other_versions(game)
    assert ps5_game in versions
    assert game not in versions                       # the current game is excluded


def test_other_versions_fallback_to_same_concept_without_igdb_match():
    concept = ConceptFactory()                        # no IGDB match -> no igdb_id
    game = GameFactory(concept=concept)
    sibling_game = GameFactory(concept=concept)       # another game in the same concept
    assert sibling_game in GameDetailView()._build_other_versions(game)


def test_family_versions_excludes_same_igdb_id_siblings():
    """Same-family concepts that SHARE this game's igdb id are 'other platforms', not family. Only a
    different-igdb concept (a remaster) belongs in family."""
    family = GameFamily.objects.create(canonical_name='Series')
    c0 = ConceptFactory(unified_title='Game', family=family)
    IGDBMatchFactory(concept=c0, igdb_id=100)
    game = GameFactory(concept=c0)
    c_same = ConceptFactory(unified_title='Game (other platform)', family=family)
    IGDBMatchFactory(concept=c_same, igdb_id=100)     # SAME igdb -> other platforms
    GameFactory(concept=c_same)
    c_remaster = ConceptFactory(unified_title='Game Remastered', family=family)
    IGDBMatchFactory(concept=c_remaster, igdb_id=200)  # DIFFERENT igdb -> family
    GameFactory(concept=c_remaster)

    fam_pks = {fv['concept'].pk for fv in GameDetailView()._build_family_versions(game)}
    assert c_remaster.pk in fam_pks
    assert c_same.pk not in fam_pks


# ── About-panel gating (drives which sections render + the empty state) ─────

def _concept_ctx(game):
    """_build_concept_context reads self.request.user (rating permissions), so give it an anonymous one."""
    view = GameDetailView()
    view.request = RequestFactory().get('/')
    view.request.user = AnonymousUser()
    return view._build_concept_context(game)


def test_concept_badges_are_the_editions_the_game_is_in(client):
    # Related badges = the grouping-badge EDITIONS a game is part of (via its stages), each a dict with a
    # showcase frame + the group it links to. (The old tier-1 Badge reference was retired with the swap.)
    from tests.factories import StageFactory, BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=['PS5'])
    series = BadgeSeriesFactory(series_slug='gow', name='God of War', badge_type='franchise')
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'])
    GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    StageFactory(series_slug='gow', stage_number=1).concepts.add(concept)

    badges = _concept_ctx(game)['badges']
    assert len(badges) == 1
    b = badges[0]
    assert b['series_slug'] == 'gow' and b['frame']          # the series + its showcase medallion frame
    assert b['type_display'] == 'Franchise' and b['group_key'] == 'ultra-hd'   # the specific edition


def test_concept_badges_only_the_editions_the_game_routes_to(client):
    # Two editions; a PS5-only game routes ONLY to Ultra HD (its platform is not in Legacy HD).
    from tests.factories import StageFactory, BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=['PS5'])
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    GroupBadgeFactory(series=series, is_live=True,
                      platform_group=PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS3']))
    GroupBadgeFactory(series=series, is_live=True,
                      platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5']))
    StageFactory(series_slug='gow', stage_number=1).concepts.add(concept)
    assert [b['group_key'] for b in _concept_ctx(game)['badges']] == ['ultra-hd']


def test_concept_badges_cross_gen_game_is_in_both_editions(client):
    from tests.factories import StageFactory, BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=['PS3', 'PS5'])   # spans both editions
    series = BadgeSeriesFactory(series_slug='tlou', name='The Last of Us')
    GroupBadgeFactory(series=series, is_live=True,
                      platform_group=PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS3']))
    GroupBadgeFactory(series=series, is_live=True,
                      platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5']))
    StageFactory(series_slug='tlou', stage_number=1).concepts.add(concept)
    assert sorted(b['group_key'] for b in _concept_ctx(game)['badges']) == ['legacy-hd', 'ultra-hd']


def test_concept_badges_exclude_series_without_a_live_group(client):
    from tests.factories import StageFactory, BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=['PS5'])
    series = BadgeSeriesFactory(series_slug='dorm', name='Dormant')
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS5'])
    GroupBadgeFactory(series=series, platform_group=pg, is_live=False)   # dormant -> not live
    StageFactory(series_slug='dorm', stage_number=1).concepts.add(concept)
    assert _concept_ctx(game)['badges'] == []                # a series with no live group isn't shown


def test_about_has_info_false_without_trusted_igdb():
    """No trusted IGDB match -> no About sections, so the panel falls back to its empty state."""
    ctx = _concept_ctx(GameFactory(concept=ConceptFactory()))
    assert ctx['about_has_info'] is False
    assert ctx['about_has_facts'] is False


def test_about_has_info_true_with_summary():
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_summary='A game about things.')
    ctx = _concept_ctx(GameFactory(concept=concept))
    assert ctx['about_has_info'] is True


def test_about_has_facts_true_with_porting_company():
    """A porting/supporting credit alone is enough to render the Quick facts card (and thus the panel)."""
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept)
    # is_developer=False so this really is the porting-only case (the factory defaults it to True).
    ConceptCompanyFactory(concept=concept, company=CompanyFactory(),
                          is_developer=False, is_porting=True)
    ctx = _concept_ctx(GameFactory(concept=concept))
    assert ctx['about_has_facts'] is True
    assert ctx['about_has_info'] is True
    assert [f['label'] for f in ctx['about_facts']] == ['Ported by']


def test_about_facts_group_companies_by_role():
    """Many supporting studios collapse into ONE 'Additional devs' row rather than one row each (a game
    like God of War Ragnarok credits seven, which used to render seven repeated labels)."""
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept)
    for _ in range(5):
        ConceptCompanyFactory(concept=concept, company=CompanyFactory(),
                              is_developer=False, is_supporting=True)

    facts = _concept_ctx(GameFactory(concept=concept))['about_facts']
    devs = [f for f in facts if f['label'] == 'Additional devs']
    assert len(devs) == 1                 # one grouped row...
    assert len(devs[0]['items']) == 5     # ...holding all five studios


def test_about_facts_lead_with_developer_and_publisher():
    """Quick facts opens with the primary credits, so it doesn't jump straight to 'Ported by'."""
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept)
    # Flags are independent booleans (a studio can be several roles at once) and the factory defaults
    # is_developer=True, so the non-developer rows have to switch it off explicitly.
    ConceptCompanyFactory(concept=concept, company=CompanyFactory(name='Santa Monica'), is_developer=True)
    ConceptCompanyFactory(concept=concept, company=CompanyFactory(name='Sony'),
                          is_developer=False, is_publisher=True)
    ConceptCompanyFactory(concept=concept, company=CompanyFactory(name='Jetpack'),
                          is_developer=False, is_porting=True)

    facts = _concept_ctx(GameFactory(concept=concept))['about_facts']
    assert [f['label'] for f in facts] == ['Developer', 'Publisher', 'Ported by']
    assert facts[0]['items'][0]['name'] == 'Santa Monica'
    assert facts[0]['items'][0]['url']  # links to the company detail page


# --- About: time-to-beat proportions -----------------------------------------
#
# The bars share ONE scale so the ratio between the estimates is the readable thing. These pin the scaling
# rules, since a wrong denominator silently produces a plausible-looking but meaningless chart.

_HOUR = 3600


def _ttb_match(hasty=10, normal=20, complete=40):
    return IGDBMatchFactory(
        time_to_beat_hastily=hasty * _HOUR if hasty else None,
        time_to_beat_normally=normal * _HOUR if normal else None,
        time_to_beat_completely=complete * _HOUR if complete else None,
    )


def test_about_ttb_scales_bars_to_the_longest_estimate():
    ttb = GameDetailView._build_about_ttb(_ttb_match(), None)

    assert [r['pct'] for r in ttb['rows']] == [25, 50, 100]
    assert [r['label'] for r in ttb['rows']] == ['Speedrun', 'Normal', 'Completionist']
    assert not any(r['is_you'] for r in ttb['rows'])
    assert ttb['comparative'] is True


def test_about_ttb_slots_viewer_between_the_estimates_they_fall_between():
    """The viewer's row sorts by TIME, not last: 15h sits between the 10h speedrun and 20h normal, so the
    column reads as one ascending scale and their position is the visible fact."""
    ttb = GameDetailView._build_about_ttb(_ttb_match(), timedelta(hours=15))

    assert [r['label'] for r in ttb['rows']] == ['Speedrun', 'You', 'Normal', 'Completionist']
    assert [r['pct'] for r in ttb['rows']] == [25, 38, 50, 100]


def test_about_ttb_viewer_sorts_after_an_estimate_they_match():
    """On a tie the estimate reads first -- "you have reached Normal" rather than displacing it."""
    ttb = GameDetailView._build_about_ttb(_ttb_match(), timedelta(hours=20))

    assert [r['label'] for r in ttb['rows']] == ['Speedrun', 'Normal', 'You', 'Completionist']
    you = next(r for r in ttb['rows'] if r['is_you'])
    assert you['pct'] == 50          # same scale as the estimates, i.e. level with Normal


def test_about_ttb_rescales_when_viewer_outruns_the_estimates():
    """Someone already past the completionist estimate must cap the scale, not overflow the track."""
    ttb = GameDetailView._build_about_ttb(_ttb_match(), timedelta(hours=100))

    assert [r['label'] for r in ttb['rows']] == ['Speedrun', 'Normal', 'Completionist', 'You']
    assert [r['pct'] for r in ttb['rows']] == [10, 20, 40, 100]


def test_about_ttb_lone_estimate_is_not_comparative():
    """One full-width bar would imply a ratio that isn't there, so the template falls back to a tally."""
    solo = _ttb_match(hasty=None, normal=20, complete=None)
    assert GameDetailView._build_about_ttb(solo, None)['comparative'] is False
    # ...but the viewer's own time gives that lone estimate something to be compared against.
    assert GameDetailView._build_about_ttb(solo, timedelta(hours=10))['comparative'] is True


def test_game_detail_renders_ttb_bars(client):
    """The comparative bars reach the page with their fill targets, ready for fillBars() to grow."""
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_summary='A summary.', time_to_beat_hastily=10 * _HOUR,
                     time_to_beat_normally=20 * _HOUR, time_to_beat_completely=40 * _HOUR)
    content = _detail(client, GameFactory(concept=concept, defined_trophies=_DEFINED))

    assert 'gd-ttb__row' in content
    assert 'data-gd-fill="100"' in content    # completionist sets the scale
    assert 'data-gd-fill="25"' in content     # speedrun against it
    assert 'Completionist' in content


def test_game_detail_renders_ttb_you_row_for_viewer_with_playtime(client):
    """The logged-in path: the viewer's own playtime renders on the same scale as the estimates."""
    # is_linked=True: _get_target_profile only resolves a LINKED profile, so an unlinked viewer would fall
    # through to the anonymous path and never get a "You" row.
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_summary='A summary.', time_to_beat_hastily=10 * _HOUR,
                     time_to_beat_normally=20 * _HOUR, time_to_beat_completely=40 * _HOUR)
    game = GameFactory(concept=concept, defined_trophies=_DEFINED)
    ProfileGameFactory(profile=profile, game=game, play_duration=timedelta(hours=20))
    client.force_login(profile.user)

    content = _detail(client, game)

    assert 'gd-ttb__row--you' in content
    assert 'data-gd-fill="50"' in content    # 20h against the 40h completionist scale


@pytest.mark.parametrize('played', [timedelta(0), timedelta(hours=-3)])
def test_about_ttb_drops_non_positive_playtime(played):
    """Zero is falsy so it was already dropped, but a NEGATIVE duration is truthy -- it would have rendered
    a "You" row with a negative bar and a "-3h" label. play_duration has no non-negative DB constraint."""
    ttb = GameDetailView._build_about_ttb(_ttb_match(), played)

    assert not any(r['is_you'] for r in ttb['rows'])
    assert [r['pct'] for r in ttb['rows']] == [25, 50, 100]


def test_about_ttb_none_without_data():
    assert GameDetailView._build_about_ttb(None, None) is None
    assert GameDetailView._build_about_ttb(_ttb_match(None, None, None), None) is None


def test_get_total_defined_trophies_tolerates_empty_blob():
    """defined_trophies defaults to {}; indexing the tiers directly used to KeyError and 500 the detail page."""
    assert GameFactory(defined_trophies={}).get_total_defined_trophies() == 0
    assert GameFactory(defined_trophies={'bronze': 3}).get_total_defined_trophies() == 3


# ── Game-detail page render (also guards the templates against syntax errors) ──

#: the view's meta-description calls get_total_defined_trophies(), which indexes these keys directly.
_DEFINED = {'bronze': 10, 'silver': 5, 'gold': 2, 'platinum': 1}


def _detail(client, game):
    url = reverse('game_detail', kwargs={'np_communication_id': game.np_communication_id})
    return client.get(url).content.decode()


def test_game_detail_renders_related_badge_editions(client):
    # The badges spine + modal render the specific EDITIONS this game is part of, each linking to its edition
    # tab (?group=<key>). Guards the template's dict fields (name / type_display / group_key / group_name).
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=['PS5'])
    from tests.factories import StageFactory, BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory
    series = BadgeSeriesFactory(series_slug='gow', name='God of War', badge_type='franchise')
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'])
    GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    StageFactory(series_slug='gow', stage_number=1).concepts.add(concept)

    content = _detail(client, game)
    assert 'gd-spine__row--badges' in content                        # the badge spine row (game is in a badge)
    assert '/badges/gow/?group=ultra-hd' in content                  # the card links to the SPECIFIC edition tab
    assert 'gd-badgecard' in content and 'Ultra HD' in content       # modal card + the edition label
    assert 'Franchise' in content                                    # the badge type


def test_game_detail_renders_about_panel(client):
    """An IGDB-enriched game renders the rebuilt About panel with its summary and grouped Quick facts."""
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_summary='A summary about the game.')
    for i in range(4):
        ConceptCompanyFactory(concept=concept, company=CompanyFactory(name=f'Studio {i}'),
                              is_developer=False, is_supporting=True)
    content = _detail(client, GameFactory(concept=concept, defined_trophies=_DEFINED))

    assert 'gd-about' in content
    assert 'About this game' in content
    assert 'A summary about the game.' in content
    # One grouped row, first 3 inline and the 4th behind the "+N more" disclosure.
    assert content.count('Additional devs') == 1
    assert 'Studio 0' in content and 'Studio 3' in content
    assert '+1 more' in content
    # The separating comma sits OUTSIDE <details> (so a wrap can't strand it on the next line), which means
    # the expanded items must not re-emit their own leading comma.
    assert ', ,' not in content and ',,' not in content


def test_game_detail_about_panel_shows_empty_state(client):
    """A game with no trusted IGDB match (and no other versions) gets the About empty state, not a blank tab.
    Deliberately leaves defined_trophies empty -- an unsynced game must still render the page."""
    content = _detail(client, GameFactory(concept=ConceptFactory()))

    assert 'No extended info yet' in content


# ── Plat card CTA ─────────────────────────────────────────────────────────

def _finished(profile, *, with_platinum=True):
    """A game whose DEFAULT trophy group this profile has finished -- the card's eligibility bar.

    NOTE the caller must pass a LINKED profile: `_get_target_profile` only resolves the viewer's own
    profile when `is_linked`, and ProfileFactory leaves that False. An unlinked profile renders the
    anonymous hero, so none of this context is built at all."""
    from tests.factories import ProfileTrophyGroupFactory, TrophyGroupFactory

    defined = {'bronze': 10, 'silver': 3, 'gold': 1, 'platinum': 1 if with_platinum else 0}
    game = GameFactory(concept=ConceptFactory(), defined_trophies=defined)
    group = TrophyGroupFactory(game=game, trophy_group_id='default', defined_trophies=defined)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=with_platinum)
    ProfileTrophyGroupFactory(profile=profile, trophy_group=group, progress=100)
    return game, group


def test_a_finished_game_offers_its_plat_card(client):
    """The reward for finishing: a LINK to the share surface, deep-linked to this completion. Not a
    modal here -- the share flow is a whole page (preview, theme picker, rating), and a second copy of
    it on game detail is the drift the rebuild removed."""
    profile = ProfileFactory(is_linked=True)
    game, group = _finished(profile, with_platinum=True)
    client.force_login(profile.user)

    content = _detail(client, game)

    assert 'gd-platcard' in content
    assert f'?c={group.id}' in content, 'must deep-link this completion, not the bare page'
    assert 'You platinumed this' in content
    assert 'gd-platcard--full' not in content


def test_a_100_percent_clear_offers_the_full_variant(client):
    profile = ProfileFactory(is_linked=True)
    game, _ = _finished(profile, with_platinum=False)
    client.force_login(profile.user)

    content = _detail(client, game)

    assert 'gd-platcard--full' in content and 'You finished this 100%' in content


def test_an_unfinished_game_offers_no_card(client):
    """`eligible_completions` is the gate, so the CTA can never offer a card the share page would deny."""
    from tests.factories import ProfileTrophyGroupFactory, TrophyGroupFactory

    profile = ProfileFactory(is_linked=True)
    game = GameFactory(concept=ConceptFactory(), defined_trophies=_DEFINED)
    group = TrophyGroupFactory(game=game, trophy_group_id='default', defined_trophies=_DEFINED)
    ProfileGameFactory(profile=profile, game=game, progress=61)
    ProfileTrophyGroupFactory(profile=profile, trophy_group=group, progress=61)
    client.force_login(profile.user)

    assert 'gd-platcard' not in _detail(client, game)


def test_another_hunters_completion_is_never_offered_as_your_card(client):
    """This page also renders someone else's progress at /games/<np>/<username>/. A card is personal, so
    linking THEIR completion would send the viewer to their own share page with a group id it refuses --
    the destination re-checks ownership with the same predicate."""
    them, me = ProfileFactory(is_linked=True), ProfileFactory(is_linked=True)
    game, _ = _finished(them, with_platinum=True)
    client.force_login(me.user)

    url = reverse('game_detail_with_profile',
                  kwargs={'np_communication_id': game.np_communication_id,
                          'psn_username': them.psn_username})
    content = client.get(url).content.decode()

    assert 'gd-platcard' not in content


def test_an_anonymous_visitor_sees_no_card_cta(client):
    profile = ProfileFactory(is_linked=True)
    game, _ = _finished(profile, with_platinum=True)

    assert 'gd-platcard' not in _detail(client, game)
