"""What PSN has called each game, over time -- the answer to "it was overwritten, from where?".

`Game.title_name` cannot answer that: `save()` cleans it unconditionally, every sync rewrites it
unless locked, and the IGDB CJK promotion replaces it and locks the replacement. So the raw string
PSN sent has never been stored, for any game, ever. These tests pin the properties that make the
observation table the answer: the name is stored RAW, history appends instead of overwriting, and
per-user noise cannot inflate it.

The trophy_titles channel is BULK-ONLY by design (one ~4-query pass per walk / per fast-path page,
after an audit showed the per-title version roughly doubled the DB cost of a whale walk), so the
semantic tests here go through `capture_title_page_bulk` -- it is the only path production takes.
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from psnawp_api.models.title_stats import PlatformCategory
from psnawp_api.models.trophies import PlatformType
from tests.factories import GameFactory
from trophies.models import PSNTitleObservation
from trophies.services.psn_metadata_service import (
    capture_title_page_bulk,
    capture_title_stats_observation,
)

pytestmark = pytest.mark.django_db


def _trophy_title(**over):
    """A trophy_titles entry shaped like psnawp's TrophyTitle, per-user fields included --
    the capture must strip those, so the fixture must carry them. np_title_id is None because
    that is what the real paginator hardcodes (only trophy_titles_for_title populates it)."""
    fields = {
        'np_communication_id': 'NPWR11111_00',
        'np_title_id': None,
        'np_service_name': 'trophy',
        'trophy_set_version': '01.00',
        'title_name': 'Ghost of Tsushima™',
        'title_detail': 'Directors Cut',
        'title_icon_url': 'https://psn/icon.png',
        'title_platform': frozenset({PlatformType.PS4}),
        'has_trophy_groups': True,
        'defined_trophies': SimpleNamespace(bronze=40, silver=10, gold=2, platinum=1),
        # Per-user fields, deliberately present:
        'progress': 37,
        'hidden_flag': False,
        'earned_trophies': SimpleNamespace(bronze=12, silver=3, gold=0, platinum=0),
        'last_updated_datetime': None,
    }
    fields.update(over)
    return SimpleNamespace(**fields)


def _stats(**over):
    fields = {
        'title_id': 'CUSA00001_00',
        'name': 'Ghost of Tsushima',
        'image_url': 'https://psn/stats.png',
        'category': PlatformCategory.PS4,
        'play_count': 12,
        'first_played_date_time': None,
        'last_played_date_time': None,
        'play_duration': None,
    }
    fields.update(over)
    return SimpleNamespace(**fields)


def _game(**over):
    fields = {'np_communication_id': 'NPWR11111_00', 'title_ids': ['CUSA00001_00']}
    fields.update(over)
    return GameFactory(**fields)


def _capture(*titles):
    return capture_title_page_bulk(list(titles))


def test_the_raw_name_is_stored_uncleaned_while_game_title_is_not():
    """THE point of the table. Game.save() strips the (TM) before the row ever lands, so the DB has
    never held what PSN actually said -- until here."""
    game = _game(title_name='Ghost of Tsushima™')

    assert _capture(_trophy_title()) == 1

    row = PSNTitleObservation.objects.get()
    assert row.title_name_raw == 'Ghost of Tsushima™', 'the raw string must survive verbatim'
    assert row.game_id == game.id, 'the FK must be linked, not silently null'
    assert row.source == 'trophy_titles'
    game.refresh_from_db()
    assert game.title_name == 'Ghost of Tsushima', 'and Game.title_name is the cleaned one'


def test_reobserving_fresh_content_neither_inserts_nor_bumps():
    """The damper: last_seen_at is provenance nothing reads below day granularity, and an undamped
    bump UPDATEd up to 400 rows on EVERY fast-path sync -- hundreds of thousands of dead tuples a
    day to move a timestamp. Within the 24h window a re-observation must be a no-op."""
    _game()
    _capture(_trophy_title())
    seen = PSNTitleObservation.objects.get().last_seen_at

    _capture(_trophy_title())

    assert PSNTitleObservation.objects.count() == 1
    assert PSNTitleObservation.objects.get().last_seen_at == seen, 'bumped inside the damper window'


def test_a_stale_row_gets_its_last_seen_bumped():
    _game()
    _capture(_trophy_title())
    PSNTitleObservation.objects.update(last_seen_at=timezone.now() - timedelta(hours=25))
    stale = PSNTitleObservation.objects.get().last_seen_at

    _capture(_trophy_title())

    assert PSNTitleObservation.objects.count() == 1
    assert PSNTitleObservation.objects.get().last_seen_at > stale


def test_a_rename_appends_a_second_row_and_keeps_the_first():
    """Append-on-change, not latest-value: a latest-value sidecar gets overwritten too and answers
    nothing about what a game USED to be called."""
    _game()

    _capture(_trophy_title())
    _capture(_trophy_title(title_name='Ghost of Tsushima DIRECTOR’S CUT™'))

    names = set(PSNTitleObservation.objects.values_list('title_name_raw', flat=True))
    assert names == {'Ghost of Tsushima™', 'Ghost of Tsushima DIRECTOR’S CUT™'}


def test_locale_alternation_settles_at_one_row_per_name():
    """The region lesson from PSNConceptData, solved without a locale key: trophy_titles names
    arrive in the syncing account's locale, so a JP sync and a US sync alternating must not
    ping-pong a column OR grow the table -- two names, two rows, forever."""
    _game()

    _capture(_trophy_title(title_name='ゴースト・オブ・ツシマ'))
    _capture(_trophy_title())
    _capture(_trophy_title(title_name='ゴースト・オブ・ツシマ'))
    _capture(_trophy_title())

    assert PSNTitleObservation.objects.count() == 2


def test_per_user_fields_cannot_split_rows():
    """Two users at different progress send byte-different payloads for the same title. If the hash
    saw per-user fields, every user would mint a row and the table would scale with syncs, not with
    renames."""
    _game()

    _capture(_trophy_title(progress=10,
                           earned_trophies=SimpleNamespace(bronze=1, silver=0, gold=0, platinum=0)))
    _capture(_trophy_title(progress=99,
                           earned_trophies=SimpleNamespace(bronze=40, silver=10, gold=2, platinum=1)))

    assert PSNTitleObservation.objects.count() == 1


def test_platform_set_order_cannot_mint_rows():
    """frozenset iteration order varies PER PROCESS (enum members hash by identity), so without the
    sorted() a PS3+PS4+PS5 cross-buy title would hash differently on every worker restart and the
    table would scale with syncs -- the one property the design forbids. The single-platform
    fixtures used everywhere else cannot discriminate this, which is how the first mutation round
    missed it."""
    _game()

    _capture(_trophy_title(title_platform=frozenset({PlatformType.PS5, PlatformType.PS4,
                                                     PlatformType.PS3})))
    _capture(_trophy_title(title_platform=frozenset({PlatformType.PS3, PlatformType.PS5,
                                                     PlatformType.PS4})))

    assert PSNTitleObservation.objects.count() == 1
    assert PSNTitleObservation.objects.get().title_platform == ['PS3', 'PS4', 'PS5']


def test_one_malformed_title_costs_only_itself():
    """The single-row path promised a bad payload costs only itself; the first bulk version broke
    that promise -- one malformed entry in a 400-title page silently lost all 400, logged as
    '0 captured'. Per-title guard now, same as the per-entry rule in _descriptions."""
    _game()
    good = _trophy_title()
    bad = SimpleNamespace(np_communication_id='NPWR11111_00')  # nothing else

    assert _capture(bad, good) == 1
    assert PSNTitleObservation.objects.get().title_name_raw == 'Ghost of Tsushima™'


def test_bulk_skips_dangling_titles_but_still_captures_the_rest():
    """Mixed page: one title with a Game row, one without. Distinguishes 'skipped correctly' from
    'did nothing' -- a fully broken bulk also returns 0 for the dangling-only case."""
    _game()
    dangling = _trophy_title(np_communication_id='NPWR99999_00')

    assert _capture(_trophy_title(), dangling) == 1

    assert not PSNTitleObservation.objects.filter(np_communication_id='NPWR99999_00').exists()
    assert PSNTitleObservation.objects.filter(np_communication_id='NPWR11111_00',
                                              source='trophy_titles').count() == 1


def test_bulk_query_count_is_invariant_in_page_size():
    """'Bounded' means invariant, not 'small at n=2'. A fixed ceiling at one size cannot detect
    per-title growth; equal counts at 2 and 20 can."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def run(n, start):
        titles = []
        for i in range(n):
            GameFactory(np_communication_id=f'NPWR{start + i:05}_00', title_ids=[])
            titles.append(_trophy_title(np_communication_id=f'NPWR{start + i:05}_00'))
        with CaptureQueriesContext(connection) as ctx:
            assert capture_title_page_bulk(titles) == n
        return len(ctx)

    small, big = run(2, 10000), run(20, 20000)

    assert small == big, f'query count grew with page size: {small} -> {big}'
    # 5 = games lookup + existing lookup + SAVEPOINT + INSERT + RELEASE (the savepoint pair is
    # counted by CaptureQueriesContext). The equality above is the real assertion.
    assert small <= 5


def test_title_stats_is_a_distinct_source_with_its_own_name():
    """title_stats carries an independent name/art/category for the same game, previously discarded
    on arrival. It must coexist with the trophy_titles row, not collide with it."""
    game = _game()
    _capture(_trophy_title())

    row = capture_title_stats_observation(game, _stats())

    assert PSNTitleObservation.objects.count() == 2
    assert row.source == 'title_stats'
    assert row.stats_category == 'ps4_game'
    assert row.game_id == game.id


def test_stats_category_stores_the_real_ps5_value():
    """PlatformCategory's values are 'unknown'/'ps4_game'/'ps5_native_game' -- verbatim enum
    values, not 'PS5'. Pinned so a reader of the column knows what to filter on."""
    row = capture_title_stats_observation(_game(), _stats(category=PlatformCategory.PS5))

    assert row.stats_category == 'ps5_native_game'


def test_the_kill_switch_stops_observation_writes(settings):
    settings.PSN_METADATA_CAPTURE_ENABLED = False
    game = _game()

    assert capture_title_page_bulk([_trophy_title()]) == 0
    assert capture_title_stats_observation(game, _stats()) is None
    assert PSNTitleObservation.objects.count() == 0


def test_a_failure_is_swallowed_and_logged(monkeypatch, caplog):
    """Runs inline in sync jobs; a capture failure must not cost a hunter their sync, and must not
    vanish either."""
    from trophies.services import psn_metadata_service as svc

    def boom(*a, **k):
        raise RuntimeError('database went away')

    monkeypatch.setattr(svc.PSNTitleObservation.objects, 'update_or_create', boom)
    with caplog.at_level('WARNING', logger=svc.logger.name):
        assert svc.capture_title_stats_observation(_game(), _stats()) is None
    assert any('title observation failed' in r.message for r in caplog.records)

    monkeypatch.setattr(svc.PSNTitleObservation.objects, 'bulk_create', boom)
    _game(np_communication_id='NPWR22222_00')
    with caplog.at_level('WARNING', logger=svc.logger.name):
        assert svc.capture_title_page_bulk(
            [_trophy_title(np_communication_id='NPWR22222_00')]) == 0
    assert any('bulk capture failed' in r.message for r in caplog.records)


def test_a_malformed_stats_payload_cannot_crash_the_sync(caplog):
    """Extraction must live INSIDE the never-raises guard: a payload missing one attribute (a
    psnawp version change) must not crash the sync walk. Note the real TrophyTitle/TitleStats are
    frozen dataclasses with mandatory fields, so this is future-proofing, not a live shape."""
    from trophies.services import psn_metadata_service as svc

    with caplog.at_level('WARNING', logger=svc.logger.name):
        assert capture_title_stats_observation(_game(), SimpleNamespace(title_id='X')) is None

    assert PSNTitleObservation.objects.count() == 0
    assert any('payload' in r.message for r in caplog.records)


# --- wiring: the lesson from the concept lane, where zero call sites were tested -----------------
#
# These are AST canaries: they catch DELETION of a call site, which is the realistic regression.
# They are shape checks, not reachability checks -- a call wrapped in dead code would still pass.
# The behavioural companion for the title_stats channel is below.

def _ast_function(path, name):
    import ast
    import pathlib

    for node in ast.walk(ast.parse(pathlib.Path(path).read_text(encoding='utf-8'))):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f'{name} not found in {path} -- renamed or moved; retarget this test')


def _direct_calls_in(node, callee):
    """Only bare-name calls (`callee(...)`), not `anything.callee(...)` -- the attribute arm let an
    unrelated receiver satisfy the first version of this helper."""
    import ast

    return [
        c for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == callee
    ]


def test_the_slow_path_bulk_captures_the_whole_walk():
    """One bulk call per walk, in _profile_refresh_slow_path AFTER the first pass (every Game row
    must exist for the capture to link). The per-title version this replaced roughly doubled the
    walk's DB cost."""
    node = _ast_function('trophies/token_keeper.py', '_profile_refresh_slow_path')
    assert len(_direct_calls_in(node, 'capture_title_page_bulk')) == 1


def test_the_fast_path_captures_page_one():
    """A pure rename changes neither trophy counts nor game count, so the fingerprint never breaks
    and the slow path never runs. Page 1 on the fast path is the ONLY channel that sees it."""
    node = _ast_function('trophies/token_keeper.py', '_job_profile_refresh')
    assert len(_direct_calls_in(node, 'capture_title_page_bulk')) == 1


def test_title_stats_wiring_behaviourally():
    """Not an AST check: the real service function, a real Game and ProfileGame, and the row must
    exist afterwards."""
    from tests.factories import ProfileFactory, ProfileGameFactory
    from trophies.services.psn_api_service import PsnApiService

    game = _game()
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=game)

    PsnApiService.update_profile_game_with_title_stats(profile, _stats())

    assert PSNTitleObservation.objects.filter(source='title_stats', game=game).exists()


def test_force_walk_structurally_gates_the_fast_path():
    """Stronger than a string match (which an inverted condition satisfied): the If that returns
    from the fast path must carry `not force_walk` as a BoolOp operand."""
    import ast

    node = _ast_function('trophies/token_keeper.py', '_job_profile_refresh')
    args = [a.arg for a in node.args.args]
    assert 'force_walk' in args

    def has_not_force_walk(test):
        return any(
            isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not)
            and isinstance(v.operand, ast.Name) and v.operand.id == 'force_walk'
            for v in (test.values if isinstance(test, ast.BoolOp) else [test])
        )

    gates = [
        t for t in ast.walk(node)
        if isinstance(t, ast.If)
        and any(isinstance(r, ast.Return) for r in ast.walk(t))
        and has_not_force_walk(t.test)
    ]
    assert gates, 'the fast-path return is no longer gated on `not force_walk`'


def test_profile_refresh_threads_the_flag_into_job_args():
    from trophies.models import Profile
    from trophies.psn_manager import PSNManager
    from tests.factories import ProfileFactory

    profile = ProfileFactory(sync_status='synced')

    with patch.object(PSNManager, 'assign_job') as assign, \
         patch.object(PSNManager, 'is_psn_outage_active', return_value=False), \
         patch('trophies.psn_manager.redis_client'):
        PSNManager.profile_refresh(profile, force_walk=True)
        forced_args = assign.call_args.kwargs.get('args', assign.call_args.args[1] if len(assign.call_args.args) > 1 else None)

        profile2 = Profile.objects.get(pk=profile.pk)
        profile2.set_sync_status('synced')
        PSNManager.profile_refresh(profile2)
        plain_args = assign.call_args.kwargs.get('args', assign.call_args.args[1] if len(assign.call_args.args) > 1 else None)

    assert forced_args == [True], 'force_walk must ride as args[0]'
    assert plain_args == [], 'the normal path must keep the old empty-args shape'


def test_an_error_profile_does_not_silently_carry_the_flag():
    """PSNManager routes sync_status='error' through initial_sync, which queues args=[] -- a forced
    walk on an error profile silently degrades. Pinned as a known fact; the backfill command
    refuses error profiles for exactly this reason."""
    from trophies.psn_manager import PSNManager
    from tests.factories import ProfileFactory

    profile = ProfileFactory(sync_status='error')

    with patch.object(PSNManager, 'assign_job') as assign, \
         patch.object(PSNManager, 'is_psn_outage_active', return_value=False), \
         patch('trophies.psn_manager.redis_client'):
        PSNManager.profile_refresh(profile, force_walk=True)

    # kwargs['args'] directly: dict.get(k, default) evaluates the default EAGERLY, so an
    # args-tuple fallback IndexErrors even when the key exists.
    assert assign.call_args.kwargs['args'] == [], (
        'error profiles route through initial_sync and the flag is dropped'
    )


def test_the_observation_admin_is_genuinely_read_only():
    """The admin docstring claims an edit would corrupt the dedup hash; the claim only holds if
    every field really is read-only and all three permissions are off."""
    from django.contrib import admin as django_admin

    model_admin = django_admin.site._registry[PSNTitleObservation]

    assert model_admin.has_add_permission(None) is False
    assert model_admin.has_change_permission(None) is False
    assert model_admin.has_delete_permission(None) is False
    assert set(model_admin.readonly_fields) == {f.name for f in PSNTitleObservation._meta.fields}
