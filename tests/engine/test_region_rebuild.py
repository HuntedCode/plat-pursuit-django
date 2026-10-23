"""The region rebuild: labels and regional flags written from where title IDs are sold, the scoped
refresh sync and the store walk use, and the store walk itself.

The shapes come from the 2026-10-23 store probes and prod baselines: Hollow Knight's EU title ID is sold
in GB/JP/HK/KR but not the US (so its list is EU/JP/AS/KR, never EU/NA), Black Myth: Wukong's single
`HP` title ID is sold in every store (so its prefix alone would wrongly read Asia-only), and Astro's
Playroom is a Sony `IP` worldwide title that used to read NA.
"""
import ast
import io
import itertools
import pathlib
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
import pytest
from django.core.management import call_command
from django.template.loader import render_to_string
from django.utils import timezone

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory
from trophies.models import Game, TitleID
from trophies.services import region_service as rs
from trophies.util_modules.constants import STOREFRONTS

pytestmark = pytest.mark.django_db

_TIDS = itertools.count(20000)
ALL_STORES = [region for _c, _l, region in STOREFRONTS]


@pytest.fixture(autouse=True)
def fake_redis():
    client = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    with patch.object(rs, 'redis_client', client):
        yield client


@pytest.fixture
def assign():
    with patch('trophies.psn_manager.PSNManager.assign_job') as assign_job:
        yield assign_job


def _tid(prefix, *, stores=None, walked=True, source=TitleID.REGION_SOURCE_DATASET, title_id=None,
         platform='PS5'):
    """A TitleID. Walked by default, with the stores agreeing with the prefix unless `stores` says
    otherwise (`[]` = sold nowhere)."""
    title_id = title_id or f'PPSA{next(_TIDS)}_00'
    if stores is None:
        stores = [r for r in [rs.normalize_region(prefix)] if r]
    TitleID.objects.create(title_id=title_id, platform=platform, region=prefix, region_source=source,
                           store_regions=stores if walked else [],
                           store_checked_at=timezone.now() if walked else None)
    return title_id


def _anchored(igdb_id, status='auto_accepted', concept_id=None):
    concept = ConceptFactory(concept_id=concept_id) if concept_id else ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id, status=status)
    return concept


def _reload(game):
    game.refresh_from_db()
    return game.region, game.is_regional


def _details(*product_ids):
    """A storefront's concept response carrying these product IDs."""
    first, *rest = product_ids or ('',)
    return {'defaultProduct': {'id': first}, 'categorizedProducts': [{'ids': list(rest)}]}


# --- rebuild: labels ----------------------------------------------------------------------

def test_rebuild_labels_a_list_from_where_its_title_ids_are_sold():
    """Hollow Knight's EU list: stored EU/NA by the storefront bug, sold everywhere but the US."""
    game = GameFactory(title_ids=[_tid('EP', stores=['EU', 'JP', 'AS', 'KR'])], region=['EU', 'NA'])
    rs.rebuild()
    assert _reload(game)[0] == ['EU', 'JP', 'AS', 'KR']


def test_an_unknown_title_id_leaves_the_label_until_walked():
    """Astro's Playroom: an IP title ID names no region, so there is no evidence to rebuild from yet.
    Its stored NA stays until the walk answers, rather than being cleared on a guess."""
    game = GameFactory(title_ids=[_tid('IP', walked=False)], region=['NA'])
    rs.rebuild()
    assert _reload(game)[0] == ['NA']


def test_a_walked_answer_replaces_na_that_came_from_an_unknown_title_id():
    game = GameFactory(title_ids=[_tid('IP', stores=['NA', 'EU', 'JP', 'AS', 'KR'])], region=['NA'])
    rs.rebuild()
    assert _reload(game)[0] == ['NA', 'EU', 'JP', 'AS', 'KR']


def test_a_walked_title_id_sold_nowhere_with_no_prefix_clears_the_label():
    game = GameFactory(title_ids=[_tid('IP', stores=[])], region=['NA'])
    rs.rebuild()
    assert _reload(game)[0] == []


def test_a_prefix_sync_wrote_is_not_evidence():
    """Before this rebuild, sync wrote the US store's answer onto auto-created TitleIDs: untrusted, so
    the label waits for the walk; the walk's answer then replaces it."""
    tid = _tid('UP', walked=False, source='')
    game = GameFactory(title_ids=[tid], region=['NA'])
    rs.rebuild()
    assert _reload(game)[0] == ['NA']
    TitleID.objects.filter(title_id=tid).update(store_regions=['EU'], store_checked_at=timezone.now())
    rs.rebuild()
    assert _reload(game)[0] == ['EU']


def test_the_migration_window_does_not_wipe_labels():
    """Right after the migration every TitleID has region_source='' until populate_title_ids re-runs. A
    rebuild in that window (sync, the nightly) must leave every label as it was."""
    games = [GameFactory(title_ids=[_tid(prefix, walked=False, source='')], region=label, is_regional=flag)
             for prefix, label, flag in [('EP', ['EU'], True), ('UP', ['NA'], True), ('JP', ['JP'], False)]]
    assert rs.rebuild() == {'lists': 3, 'regions': 0, 'flagged': 0, 'unflagged': 0}
    assert [_reload(g) for g in games] == [(['EU'], True), (['NA'], True), (['JP'], False)]


def test_an_unwalked_dataset_prefix_labels_provisionally():
    game = GameFactory(title_ids=[_tid('EP', walked=False)], region=['EU', 'NA'])
    rs.rebuild()
    assert _reload(game)[0] == ['EU']


def test_a_list_without_title_ids_keeps_its_label():
    """PS3/Vita lists have no title IDs, so there is nothing to rebuild from."""
    game = GameFactory(title_platform=['PSVITA'], title_ids=[], region=['JP'])
    assert rs.rebuild()['regions'] == 0
    assert _reload(game)[0] == ['JP']


def test_the_script_of_the_title_labels_a_list_its_title_ids_cannot():
    game = GameFactory(title_name='ゲームのタイトル', title_ids=[_tid('IP', stores=[])], region=['NA'])
    rs.rebuild()
    assert _reload(game)[0] == ['JP']


def test_a_reordered_label_is_not_a_change():
    game = GameFactory(title_ids=[_tid('UP'), _tid('EP')], region=['EU', 'NA'])
    assert rs.rebuild()['regions'] == 0
    assert _reload(game)[0] == ['EU', 'NA']


def test_a_region_locked_list_is_never_written():
    game = GameFactory(title_ids=[_tid('EP')], region=['NA'], is_regional=True, region_lock=True)
    rs.rebuild()
    assert _reload(game) == (['NA'], True)


def test_a_lock_set_after_the_load_wins(monkeypatch):
    """The nightly loads, then writes. An admin who locks a list in between keeps their region."""
    game = GameFactory(title_ids=[_tid('EP')], region=['NA'], is_regional=True)
    real_load = rs._load

    def load_then_admin_locks(*args, **kwargs):
        loaded = real_load(*args, **kwargs)
        # Only the lock changes, so the loaded-value condition still matches: the lock alone must stop it.
        Game.objects.filter(pk=game.pk).update(region_lock=True)
        return loaded

    monkeypatch.setattr(rs, '_load', load_then_admin_locks)
    assert rs.rebuild()['regions'] == 0
    assert _reload(game) == (['NA'], True)


def test_a_lock_set_after_the_load_keeps_the_flag_off(monkeypatch):
    """The flag-ON write is conditional too: a walked JP-only list the rebuild would flag, locked by an
    admin as not regional between load and write, stays unflagged."""
    game = GameFactory(title_ids=[_tid('JP')], region=['JP'], is_regional=False)
    real_load = rs._load

    def load_then_admin_locks(*args, **kwargs):
        loaded = real_load(*args, **kwargs)
        Game.objects.filter(pk=game.pk).update(region_lock=True)
        return loaded

    monkeypatch.setattr(rs, '_load', load_then_admin_locks)
    # Counts are what was WRITTEN: the lock won, so nothing was flagged.
    assert rs.rebuild()['flagged'] == 0
    assert _reload(game)[1] is False


def test_a_label_changed_after_the_load_wins(monkeypatch):
    """A walk that rebuilt the list after this rebuild loaded it holds the newer answer."""
    game = GameFactory(title_ids=[_tid('EP')], region=['NA'])
    real_load = rs._load

    def load_then_walk_rebuilds(*args, **kwargs):
        loaded = real_load(*args, **kwargs)
        Game.objects.filter(pk=game.pk).update(region=['EU', 'JP'])
        return loaded

    monkeypatch.setattr(rs, '_load', load_then_walk_rebuilds)
    assert rs.rebuild()['regions'] == 0
    assert _reload(game)[0] == ['EU', 'JP']


@pytest.mark.parametrize('raw', ['JP', ['JP', ''], ['JP', 'JP']])
def test_a_legacy_shaped_label_is_rewritten_as_a_clean_list(raw):
    """jsonb equality is exact, so a write conditional on the NORMALISED value would never match these
    rows, and a bare string renders in the chip loop as one chip per letter."""
    game = GameFactory(title_ids=[_tid('JP')], region=raw)
    assert rs.rebuild()['regions'] == 1
    assert _reload(game) == (['JP'], True)


def test_a_legacy_label_with_no_evidence_is_still_normalised():
    game = GameFactory(title_platform=['PSVITA'], title_ids=[], region='JP')
    rs.rebuild()
    assert _reload(game)[0] == ['JP']


def test_a_list_partly_walked_keeps_its_label_until_every_title_id_is():
    """One title ID walked and sold nowhere (no prefix), one not walked yet: "names no region" only
    means "not known yet", so the stored NA stays."""
    game = GameFactory(title_ids=[_tid('IP', stores=[]), _tid('UP', walked=False, source='')], region=['NA'])
    rs.rebuild()
    assert _reload(game)[0] == ['NA']


def test_a_second_rebuild_writes_nothing():
    GameFactory(title_ids=[_tid('EP')], region=['NA'])
    rs.rebuild()
    assert rs.rebuild() == {'lists': 1, 'regions': 0, 'flagged': 0, 'unflagged': 0}


def test_dry_run_counts_and_writes_nothing():
    game = GameFactory(title_ids=[_tid('EP')], region=['NA'])
    counts = rs.rebuild(dry_run=True)
    assert counts['regions'] == 1
    assert _reload(game)[0] == ['NA']


# --- rebuild: flags -----------------------------------------------------------------------

def test_a_walked_regional_split_is_flagged():
    concept = _anchored(14593)
    na = GameFactory(concept=concept, title_ids=[_tid('UP', stores=['NA'])], region=['NA'])
    eu = GameFactory(concept=concept, title_ids=[_tid('EP', stores=['EU', 'JP', 'AS', 'KR'])], region=['EU'])
    counts = rs.rebuild()
    assert counts['flagged'] == 2
    assert _reload(na) == (['NA'], True)
    assert _reload(eu) == (['EU', 'JP', 'AS', 'KR'], True)


def test_same_region_split_lists_are_unflagged():
    """Dead by Daylight: one game's trophies split across lists, all from the same regions."""
    concept = _anchored(18866)
    parts = [GameFactory(concept=concept, title_ids=[_tid('UP'), _tid('EP')], region=['NA', 'EU'],
                         is_regional=True) for _ in range(2)]
    assert rs.rebuild()['unflagged'] == 2
    assert all(_reload(game)[1] is False for game in parts)


def test_a_flag_never_acts_on_an_unwalked_prefix():
    """Wukong by prefix alone is an Asia-only list. Until the walk answers, its flag must not move."""
    game = GameFactory(title_ids=[_tid('HP', walked=False)], region=[], is_regional=False)
    rs.rebuild()
    assert _reload(game) == (['AS'], False)


def test_a_worldwide_product_walked_reads_global():
    game = GameFactory(title_ids=[_tid('HP', stores=['NA', 'EU', 'JP', 'AS', 'KR'])], region=['AS'],
                       is_regional=True)
    rs.rebuild()
    assert _reload(game) == (['NA', 'EU', 'JP', 'AS', 'KR'], False)


def test_a_walked_asia_only_lone_list_is_flagged():
    game = GameFactory(title_ids=[_tid('JP', stores=['JP'])], region=['JP'], is_regional=False)
    rs.rebuild()
    assert _reload(game)[1] is True


def test_an_unjudged_list_keeps_its_flag():
    flagged = GameFactory(title_ids=[], region=['NA'], is_regional=True)
    unflagged = GameFactory(title_ids=[_tid('IP', walked=False)], region=[], is_regional=False)
    rs.rebuild()
    assert _reload(flagged)[1] is True
    assert _reload(unflagged)[1] is False


# --- scoping ------------------------------------------------------------------------------

def test_unit_queryset_holds_every_list_of_the_seeds_units():
    target = GameFactory(concept=_anchored(204580, concept_id='204580'))
    x_n = GameFactory(concept=_anchored(204580, concept_id='204580-2'))
    other_version = GameFactory(concept=_anchored(5328))
    untrusted = _anchored(777, status='pending_review')
    loose = GameFactory(concept=untrusted)
    loose_sibling = GameFactory(concept=untrusted)
    same_igdb_untrusted = GameFactory(concept=_anchored(777, status='pending_review'))
    pks = set(rs.unit_queryset([target.pk, loose.pk]).values_list('pk', flat=True))
    assert pks == {target.pk, x_n.pk, loose.pk, loose_sibling.pk}
    assert other_version.pk not in pks and same_igdb_untrusted.pk not in pks


def test_refresh_units_leaves_other_units_alone():
    touched = GameFactory(concept=_anchored(40), title_ids=[_tid('EP')], region=['NA'])
    elsewhere = GameFactory(concept=_anchored(41), title_ids=[_tid('EP')], region=['NA'])
    rs.refresh_units([touched.pk])
    assert _reload(touched)[0] == ['EU']
    assert _reload(elsewhere)[0] == ['NA']


def test_refresh_units_with_nothing_does_nothing():
    assert rs.refresh_units([]) is None


# --- the store walk -----------------------------------------------------------------------

@pytest.mark.parametrize('details,sold', [
    (_details('EP9000-PPSA03208_00-GHOSTSHIP0000000'), True),
    (_details('UP9000-PPSA02225_00-GHOSTDIRECTORPS5', 'EP9000-PPSA03208_00-GHOSTSHIP0000000'), True),
    # The concept as the asked store sells it: another title ID's products, not this one's.
    (_details('UP9000-PPSA02225_00-GHOSTDIRECTORPS5', 'UP9000-CUSA11456_00-GHOSTDIRECTORCUT'), False),
    ({'id': 235227}, False),
    ({}, False),
    (None, False),
    ({'defaultProduct': None, 'categorizedProducts': None}, False),
])
def test_sells_title_id_needs_the_exact_title_id(details, sold):
    assert rs.sells_title_id(details, 'PPSA03208_00') is sold


def test_record_store_answers_saves_and_rebuilds(fake_redis):
    tid = _tid('EP', walked=False, title_id='CUSA13285_00', platform='PS4')
    game = GameFactory(title_platform=['PS4'], title_ids=[tid], region=['EU', 'NA'])
    fake_redis.set(rs.QUEUED_KEY.format(title_id=tid), 1)
    answers = {region: _details() for region in ALL_STORES}
    for region in ('EU', 'JP', 'AS', 'KR'):
        answers[region] = _details(f'EP0001-{tid}-HOLLOWKNIGHT0000')
    assert rs.record_store_answers(tid, answers) == ['EU', 'JP', 'AS', 'KR']
    row = TitleID.objects.get(title_id=tid)
    assert row.store_regions == ['EU', 'JP', 'AS', 'KR'] and row.store_checked_at is not None
    assert _reload(game)[0] == ['EU', 'JP', 'AS', 'KR']
    assert not fake_redis.exists(rs.QUEUED_KEY.format(title_id=tid))


def test_an_answer_for_a_missing_title_id_row_is_kept():
    """A row deleted between queue and answer must not drop six calls' worth of answers."""
    answers = {region: _details() for region in ALL_STORES}
    answers['JP'] = _details('JP0001-PPSA70050_00-GAME000000000000')
    assert rs.record_store_answers('PPSA70050_00', answers) == ['JP']
    row = TitleID.objects.get(title_id='PPSA70050_00')
    assert (row.platform, row.store_regions) == ('PS5', ['JP']) and row.store_checked_at is not None


def test_an_answer_keeps_an_existing_rows_platform():
    """update_or_create's create-only platform must not overwrite a row's own (a PPSA ID filed as PS4
    would otherwise be "corrected" by the prefix rule)."""
    tid = _tid('EP', walked=False, title_id='PPSA70051_00', platform='PS4')
    rs.record_store_answers(tid, {region: _details() for region in ALL_STORES})
    assert TitleID.objects.get(title_id=tid).platform == 'PS4'


def test_a_title_id_sold_nowhere_is_recorded_as_walked():
    tid = _tid('EP', walked=False)
    assert rs.record_store_answers(tid, {region: _details() for region in ALL_STORES}) == []
    assert TitleID.objects.get(title_id=tid).store_checked_at is not None


def test_record_store_answers_refuses_a_partial_walk():
    tid = _tid('EP', walked=False)
    with pytest.raises(ValueError):
        rs.record_store_answers(tid, {'NA': _details()})
    assert TitleID.objects.get(title_id=tid).store_checked_at is None


def test_walk_title_id_asks_every_storefront():
    tid = _tid('HP', walked=False)
    asked = []

    def fetch(country, language):
        asked.append((country, language))
        return _details(f'HP0001-{tid}-WUKONG0000000000')

    assert rs.walk_title_id(tid, fetch) == ALL_STORES
    assert asked == [(c, l) for c, l, _r in STOREFRONTS]


def test_a_chain_walk_keeps_the_chain_lock_alive(fake_redis):
    """A 500-ID batch can take hours to drain at the token rate; without this the lock lapses mid-batch
    and the nightly starts a second chain."""
    fake_redis.set(rs.CHAIN_KEY, 1, ex=60)
    tid = _tid('EP', walked=False)
    rs.walk_title_id(tid, lambda country, language: _details(), chain=True)
    assert fake_redis.ttl(rs.CHAIN_KEY) > 60


def test_a_sync_walk_neither_refreshes_nor_creates_the_chain_lock(fake_redis):
    fake_redis.set(rs.CHAIN_KEY, 1, ex=60)
    rs.walk_title_id(_tid('EP', walked=False), lambda country, language: _details())
    assert fake_redis.ttl(rs.CHAIN_KEY) <= 60
    fake_redis.delete(rs.CHAIN_KEY)
    rs.walk_title_id(_tid('EP', walked=False), lambda country, language: _details(), chain=True)
    assert not rs.walk_is_running()


def test_the_queued_key_outlives_a_long_bulk_queue():
    assert rs.QUEUED_TTL_SECONDS >= 24 * 3600


def test_a_failed_store_call_writes_nothing():
    tid = _tid('EP', walked=False)
    calls = iter([_details(), _details()])

    def fetch(country, language):
        return next(calls)  # the third store raises StopIteration, like a failed API call

    with pytest.raises(StopIteration):
        rs.walk_title_id(tid, fetch)
    row = TitleID.objects.get(title_id=tid)
    assert row.store_checked_at is None and row.store_regions == []


def test_walk_candidates_order_skips_and_platforms(fake_redis):
    walked = _tid('UP')
    queued = _tid('EP', walked=False)
    ps4 = _tid('EP', walked=False, title_id='CUSA70001_00', platform='PS4')
    popular = _tid('JP', walked=False)
    GameFactory(title_ids=[walked, queued], played_count=999)
    GameFactory(title_ids=[popular, 'PPSA70002_00'], played_count=500, np_communication_id='NPWR70000_00')
    GameFactory(title_ids=[ps4, 'CUSA70003_00'], played_count=10)
    GameFactory(title_ids=[popular], played_count=1)
    fake_redis.set(rs.QUEUED_KEY.format(title_id=queued), 1)
    picked = rs.walk_candidates()
    assert [tid for tid, _p, _n in picked] == [popular, 'PPSA70002_00', ps4, 'CUSA70003_00']
    assert picked[0] == (popular, 'PS5', 'NPWR70000_00')
    assert dict((tid, plat) for tid, plat, _n in picked)['CUSA70003_00'] == 'PS4'
    assert len(rs.walk_candidates(limit=2)) == 2


def test_unwalked_count():
    GameFactory(title_ids=[_tid('UP'), _tid('EP', walked=False), 'PPSA70010_00'])
    GameFactory(title_ids=['PPSA70010_00'])
    assert rs.unwalked_count() == 2


def test_queue_lookup_is_profile_less_uncounted_and_deduped(assign):
    assert rs.queue_lookup('PPSA70020_00', 'PS5', 'NPWR70020_00', queue='bulk_priority') is True
    assert rs.queue_lookup('PPSA70020_00', 'PS5', 'NPWR70020_00', queue='bulk_priority') is False
    assign.assert_called_once_with(rs.WALK_JOB, ['PPSA70020_00', 'PS5', 'NPWR70020_00', False], profile_id=None,
                                   priority_override='bulk_priority', skip_counter=True)
    assert TitleID.objects.filter(title_id='PPSA70020_00', platform='PS5').exists()


def test_queue_lookup_defaults_to_the_bulk_queue(assign):
    rs.queue_lookup('PPSA70021_00', 'PS5', 'NPWR70021_00')
    assert assign.call_args.kwargs['priority_override'] == "bulk_priority"


def test_refill_queues_a_batch_then_a_refill_behind_it(assign, fake_redis):
    GameFactory(title_ids=[_tid('UP')] + [_tid('UP', walked=False) for _ in range(3)])
    assert rs.refill_walk(2) == (2, 3)
    jobs = [c.args[0] for c in assign.call_args_list]
    assert jobs == [rs.WALK_JOB, rs.WALK_JOB, rs.REFILL_JOB]
    # The chain's own lookups say so, which is what lets them keep the chain lock alive.
    assert all(c.args[1][3] is True for c in assign.call_args_list[:2])
    # The refill carries the WALKED count it started from, its progress measure.
    assert assign.call_args_list[-1].args[1] == [2, 1]
    assert all(c.kwargs == {'profile_id': None, 'priority_override': 'bulk_priority', 'skip_counter': True}
               for c in assign.call_args_list)
    assert rs.walk_is_running()


def test_refill_stops_when_a_batch_walked_nothing(assign, fake_redis):
    GameFactory(title_ids=[_tid('UP'), _tid('UP', walked=False)])
    fake_redis.set(rs.CHAIN_KEY, 1)
    assert rs.refill_walk(10, last_walked=1) == (0, 1)
    assign.assert_not_called()
    assert not rs.walk_is_running()


def test_new_title_ids_from_sync_do_not_read_as_no_progress(assign):
    """Sync adds title IDs while the walk runs. The unwalked count can go UP across a batch that
    walked plenty, so progress is measured on the walked count."""
    GameFactory(title_ids=[_tid('UP'), _tid('UP')] + [_tid('UP', walked=False) for _ in range(5)])
    assert rs.refill_walk(1, last_walked=1) == (1, 5)


def test_refill_pauses_when_everything_left_is_cooling_down(assign, fake_redis, caplog):
    tid = _tid('UP', walked=False)
    GameFactory(title_ids=[tid])
    fake_redis.set(rs.QUEUED_KEY.format(title_id=tid), 1)
    fake_redis.set(rs.CHAIN_KEY, 1)
    with caplog.at_level('WARNING', logger='psn_api'):
        assert rs.refill_walk(10) == (0, 1)
    assign.assert_not_called()
    assert 'cooling down' in caplog.text
    assert not rs.walk_is_running()


def test_refill_finishes_quietly_when_everything_is_walked(assign, caplog):
    GameFactory(title_ids=[_tid('UP')])
    with caplog.at_level('WARNING', logger='psn_api'):
        assert rs.refill_walk(10) == (0, 0)
    assert caplog.text == ''


def test_start_walk_takes_the_lock_once(assign, fake_redis):
    assert rs.start_walk(100) is True
    assert rs.start_walk(100) is False
    assign.assert_called_once_with(rs.REFILL_JOB, [100, None], profile_id=None,
                                   priority_override='bulk_priority', skip_counter=True)


def test_a_failed_start_releases_the_lock(fake_redis):
    with patch('trophies.psn_manager.PSNManager.assign_job', side_effect=RuntimeError('redis down')):
        with pytest.raises(RuntimeError):
            rs.start_walk(100)
    assert not rs.walk_is_running()


def test_a_failed_queue_releases_the_key(fake_redis):
    with patch('trophies.psn_manager.PSNManager.assign_job', side_effect=RuntimeError('redis down')):
        with pytest.raises(RuntimeError):
            rs.queue_lookup('PPSA70040_00', 'PS5', 'NPWR70040_00')
    assert not fake_redis.exists(rs.QUEUED_KEY.format(title_id='PPSA70040_00'))


@pytest.mark.parametrize('title_id,known,platform', [
    ('PPSA00001_00', 'PS5', 'PS5'), ('CUSA00001_00', 'PS4', 'PS4'),
    ('PPSA00001_00', 'PSVR2', 'PS5'), ('CUSA00001_00', 'PSVR', 'PS4'),
    ('PPSA00001_00', None, 'PS5'), ('CUSA00001_00', '', 'PS4'),
])
def test_platform_for_takes_only_a_console(title_id, known, platform):
    assert rs.platform_for(title_id, known) == platform


def test_after_sync_rebuilds_and_queues_an_unwalked_title_id(assign):
    tid = _tid('EP', walked=False)
    game = GameFactory(title_ids=[tid], region=['EU', 'NA'])
    rs.after_title_id_synced(game, TitleID.objects.get(title_id=tid))
    assert _reload(game)[0] == ['EU']
    # chain=False: a sync-queued walk must never keep a dead chain's lock alive.
    assert assign.call_args.args[:2] == (rs.WALK_JOB, [tid, 'PS5', game.np_communication_id, False])
    # Bulk, below every hunter's sync jobs, whichever queue sync_title_id itself ran on.
    assert assign.call_args.kwargs['priority_override'] == 'bulk_priority'
    assert assign.call_args.kwargs['skip_counter'] is True


def test_after_sync_does_not_queue_a_walked_title_id(assign):
    tid = _tid('EP')
    game = GameFactory(title_ids=[tid])
    rs.after_title_id_synced(game, TitleID.objects.get(title_id=tid))
    assign.assert_not_called()


def test_apply_script_region():
    game = GameFactory(region=['NA'], is_regional=False)
    rs.apply_script_region(game, 'JP')
    assert _reload(game) == (['NA', 'JP'], True)
    locked = GameFactory(region=['NA'], region_lock=True)
    rs.apply_script_region(locked, 'JP')
    assert _reload(locked) == (['NA'], False)
    rs.apply_script_region(game, 'Unknown')
    assert _reload(game)[0] == ['NA', 'JP']


# --- populate_title_ids -------------------------------------------------------------------

def test_populate_title_ids_marks_the_dataset_and_never_writes_ip():
    TitleID.objects.create(title_id='CUSA00029_00', platform='PS4', region='UP', store_regions=['EU'],
                           store_checked_at=timezone.now())
    tsv = ('titleId\tconceptId\tname\tcontentId\tregion\tpublisherId\n'
           'CUSA00029_00\t205264\tWATCH_DOGS\t\t\t\n'
           'CUSA02834_00\t1\tKorean\tKP5014-CUSA02834_00-SM7EMPGAMEKR0000\tKP\tKP5014\n')
    response = SimpleNamespace(text=tsv, raise_for_status=lambda: None)
    with patch('core.management.commands.populate_title_ids.requests.get', return_value=response):
        call_command('populate_title_ids', stdout=io.StringIO())
    watch_dogs = TitleID.objects.get(title_id='CUSA00029_00')
    assert (watch_dogs.region, watch_dogs.region_source) == ('', TitleID.REGION_SOURCE_DATASET)
    assert watch_dogs.store_regions == ['EU'] and watch_dogs.store_checked_at is not None
    korean = TitleID.objects.get(title_id='CUSA02834_00')
    assert (korean.region, korean.region_source) == ('KP', TitleID.REGION_SOURCE_DATASET)


# --- the commands -------------------------------------------------------------------------

def _run(name, *args):
    out = io.StringIO()
    call_command(name, *args, stdout=out)
    return out.getvalue()


def test_rebuild_regions_command():
    game = GameFactory(title_ids=[_tid('EP')], region=['NA'])
    assert 'would change 1 region labels' in _run('rebuild_regions', '--dry-run')
    assert _reload(game)[0] == ['NA']
    assert 'changed 1 region labels' in _run('rebuild_regions')
    assert _reload(game)[0] == ['EU']


def test_resolve_title_regions_status_limit_and_full(assign):
    GameFactory(title_ids=[_tid('UP', walked=False) for _ in range(3)])
    assert '3 title IDs on lists still to walk.' in _run('resolve_title_regions', '--status')
    assign.assert_not_called()
    assert 'Queued 2 title IDs (no chain)' in _run('resolve_title_regions', '--limit', '2')
    assert [c.args[0] for c in assign.call_args_list] == [rs.WALK_JOB, rs.WALK_JOB]
    assign.reset_mock()
    assert 'Started the walk: 3 title IDs to go' in _run('resolve_title_regions', '--batch', '50')
    assign.assert_called_once_with(rs.REFILL_JOB, [50, None], profile_id=None,
                                   priority_override='bulk_priority', skip_counter=True)
    assign.reset_mock()
    assert 'A walk is already running' in _run('resolve_title_regions')
    assign.assert_not_called()


def test_resolve_title_regions_when_everything_is_walked(assign):
    GameFactory(title_ids=[_tid('UP')])
    assert '0 title IDs on lists still to walk.' in _run('resolve_title_regions')
    assign.assert_not_called()


def test_the_nightly_rebuilds_regions_then_resumes_the_walk():
    """The walk resumes nightly: a failed lookup is otherwise never retried, and title IDs sync never
    queued (the no-details branch) never walked."""
    from core.management.commands.nightly import STEPS

    labels = [label for label, _cmd, _kw in STEPS]
    assert ('region rebuild', 'rebuild_regions', {}) in STEPS
    assert ('region walk', 'resolve_title_regions', {}) in STEPS
    assert labels.index('region walk') > labels.index('region rebuild')


def test_the_redis_flush_clears_the_walk_keys():
    source = pathlib.Path('trophies/management/commands/redis_admin.py').read_text(encoding='utf-8')
    flush = ast.unparse(next(n for n in ast.walk(ast.parse(source))
                             if isinstance(n, ast.FunctionDef) and n.name == '_handle_flush_token_keeper'))
    assert "'region_walk:*'" in flush


# --- the badge partial --------------------------------------------------------------------

def test_region_badges_print_korea_and_china_as_themselves():
    html = render_to_string('partials/region_badges.html',
                            {'game': SimpleNamespace(is_regional=True, region=['KR', 'CN', 'AS'])})
    assert '>KR<' in html and '>CN<' in html and '>AS<' in html


# --- the worker wiring (read with ast: importing token_keeper registers an atexit Redis hook) ----

def _token_keeper_tree():
    return ast.parse(pathlib.Path('trophies/token_keeper.py').read_text(encoding='utf-8'))


def _method(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_walk_job_dispatch_binds_to_its_signature():
    tree = _token_keeper_tree()
    params = [a.arg for a in _method(tree, '_job_resolve_title_id_region').args.args]
    assert params == ['self', 'title_id', 'platform', 'np_communication_id', 'chain']
    # queue_lookup queues [title_id, platform, np_communication_id, chain]; chain defaults for older jobs.
    dispatch = next(
        n for n in ast.walk(_method(tree, '_job_worker_loop'))
        if isinstance(n, ast.Call) and getattr(n.func, 'attr', '') == '_job_resolve_title_id_region'
    )
    assert [ast.unparse(a) for a in dispatch.args] == ['args[0]', 'args[1]', 'args[2]', 'len(args) > 3 and args[3]']


def test_the_walk_resolves_its_platform_before_taking_a_token():
    """A bad platform raising INSIDE the call would leave the instance acquired for it busy."""
    method = _method(_token_keeper_tree(), '_job_resolve_title_id_region')
    fetch = next(n for n in method.body if isinstance(n, ast.FunctionDef) and n.name == 'fetch')
    outside = [n for n in method.body if n is not fetch]
    assert any('PlatformType(' in ast.unparse(n) for n in outside)
    assert 'PlatformType(' not in ast.unparse(fetch)


def test_profile_less_api_calls_do_not_touch_a_profile():
    """The store walk calls the API with profile=None; the success path must not dereference it."""
    method = _method(_token_keeper_tree(), '_execute_api_call')
    guarded = [
        n for n in ast.walk(method)
        if isinstance(n, ast.If) and any(
            isinstance(c, ast.Call) and getattr(c.func, 'attr', '') == 'set_history_public_flag'
            and [ast.unparse(a) for a in c.args] == ['True']
            for c in ast.walk(n))
    ]
    assert guarded, 'set_history_public_flag(True) is no longer inside an if'
    assert 'profile' in {x.id for x in ast.walk(guarded[0].test) if isinstance(x, ast.Name)}


def test_sync_no_longer_reads_a_region_from_details():
    source = ast.unparse(_method(_token_keeper_tree(), '_job_sync_title_id'))
    assert 'after_title_id_synced' in source
    assert 'detect_region_from_details' not in source and 'add_region' not in source
    assert "'IP'" not in source


# --- the model ----------------------------------------------------------------------------

def test_a_new_title_id_has_no_store_answer():
    row = TitleID.objects.create(title_id='PPSA70030_00', platform='PS5')
    assert (row.region, row.region_source, row.store_regions, row.store_checked_at) == ('', '', [], None)
    assert Game.objects.count() == 0
