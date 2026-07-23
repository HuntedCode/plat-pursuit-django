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
