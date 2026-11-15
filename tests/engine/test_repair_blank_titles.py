"""repair_blank_titles: fill blank Game titles from observed PSN names, rename the "(PS4)" stubs a
blank title produced, and re-anchor them.

Regression context (Nov 2026): 86 Games had a blank title_name, all with a real name in
PSNTitleObservation. PSN had sent a blank trophyTitleName on some syncs, which overwrote the title;
stubs minted afterwards were named "(PS4)" and had no title for IGDB to match on.
"""
import itertools
from unittest.mock import patch

import pytest
from django.core.management import call_command

from tests.factories import ConceptFactory, GameFactory
from trophies.models import PSNTitleObservation

pytestmark = pytest.mark.django_db

ANCHOR_CALL = 'trophies.management.commands.repair_blank_titles.call_command'
_hashes = itertools.count()


def _observe(game, name, source='trophy_titles'):
    PSNTitleObservation.objects.create(
        np_communication_id=game.np_communication_id, game=game, source=source,
        title_name_raw=name, content_hash=f'hash-{next(_hashes)}',
    )


def _blank_game(observed=None, **kwargs):
    kwargs.setdefault('title_platform', ['PS4'])
    game = GameFactory(title_name='', **kwargs)
    _observe(game, '')  # PSN really sent a blank: every affected game had one on record
    if observed:
        _observe(game, observed)
    return game


def _run(*args):
    with patch(ANCHOR_CALL) as anchor:
        call_command('repair_blank_titles', *args)
    return anchor


def test_fills_a_blank_title_from_the_observed_name_cleaned():
    game = _blank_game(observed='Prince of Persia®: The Sands of Time Remake')

    _run()

    game.refresh_from_db()
    assert game.title_name == 'Prince of Persia: The Sands of Time Remake'


def test_a_game_with_no_observed_name_is_reported_not_guessed(capsys):
    game = _blank_game()

    _run()

    game.refresh_from_db()
    assert game.title_name == ''
    assert f'[needs a hand fix] {game.np_communication_id}' in capsys.readouterr().out


def test_a_locked_blank_title_is_left_alone():
    game = _blank_game(observed='Rising Dusk', lock_title=True)

    _run()

    game.refresh_from_db()
    assert game.title_name == ''


def test_renames_a_junk_stub_and_regenerates_its_slug():
    stub = ConceptFactory(concept_id='PP_90001', unified_title='(PS4)')
    assert stub.slug == 'ps4'
    _blank_game(observed='Rising Dusk', concept=stub)

    _run()

    stub.refresh_from_db()
    assert stub.unified_title == 'Rising Dusk (PS4)'
    assert stub.slug == 'rising-dusk-ps4'


def test_a_junk_stub_is_renamed_even_when_a_later_walk_already_refilled_its_title():
    # The sync guard fills a blank title on the next walk that names it, but the stub minted
    # while it was blank keeps its "(PS4)" name until this command runs.
    stub = ConceptFactory(concept_id='PP_90008', unified_title='(PS4)')
    GameFactory(title_name='Rising Dusk', title_platform=['PS4'], concept=stub)

    anchor = _run()

    stub.refresh_from_db()
    assert stub.unified_title == 'Rising Dusk (PS4)'
    assert anchor.call_args.kwargs['concept'] == 'PP_90008'


def test_a_real_title_that_starts_with_a_parenthesis_is_not_junk():
    stub = ConceptFactory(concept_id='PP_90002', unified_title='(Not) Alone (PS4)')
    GameFactory(title_name='(Not) Alone', concept=stub)

    anchor = _run()

    stub.refresh_from_db()
    assert stub.unified_title == '(Not) Alone (PS4)'
    anchor.assert_not_called()


def test_only_pp_stubs_are_renamed():
    anchored = ConceptFactory(concept_id='292278', unified_title='(PS4)')
    _blank_game(observed="Ringo's Roundup", concept=anchored)

    _run()

    anchored.refresh_from_db()
    assert anchored.unified_title == '(PS4)'


def test_a_title_locked_stub_is_not_renamed():
    stub = ConceptFactory(concept_id='PP_90003', unified_title='(PS4)', title_lock=True)
    _blank_game(observed='Rising Dusk', concept=stub)

    _run()

    stub.refresh_from_db()
    assert stub.unified_title == '(PS4)'


def test_re_anchors_each_renamed_stub():
    stub = ConceptFactory(concept_id='PP_90004', unified_title='(PS4)')
    _blank_game(observed='Rising Dusk', concept=stub)

    anchor = _run()

    anchor.assert_called_once()
    assert anchor.call_args.args == ('anchor_concepts',)
    assert anchor.call_args.kwargs['concept'] == 'PP_90004'


def test_one_failed_anchor_does_not_stop_the_rest():
    for n in (1, 2):
        stub = ConceptFactory(concept_id=f'PP_9100{n}', unified_title='(PS4)')
        _blank_game(observed=f'Game {n}', concept=stub)

    with patch(ANCHOR_CALL, side_effect=[RuntimeError('IGDB down'), None]) as anchor:
        call_command('repair_blank_titles')

    assert anchor.call_count == 2


def test_no_anchor_skips_the_anchor_pass():
    stub = ConceptFactory(concept_id='PP_90005', unified_title='(PS4)')
    _blank_game(observed='Rising Dusk', concept=stub)

    anchor = _run('--no-anchor')

    anchor.assert_not_called()
    stub.refresh_from_db()
    assert stub.unified_title == 'Rising Dusk (PS4)'


def test_dry_run_writes_nothing_but_previews_the_rename(capsys):
    stub = ConceptFactory(concept_id='PP_90006', unified_title='(PS4)')
    game = _blank_game(observed='Rising Dusk', concept=stub)

    anchor = _run('--dry-run')

    game.refresh_from_db()
    stub.refresh_from_db()
    assert game.title_name == ''
    assert stub.unified_title == '(PS4)'
    anchor.assert_not_called()
    # The rename preview reads pass 1's plan, since pass 1 wrote nothing.
    assert "'(PS4)' -> 'Rising Dusk (PS4)'" in capsys.readouterr().out


def test_a_stub_whose_games_are_all_still_untitled_is_reported(capsys):
    stub = ConceptFactory(concept_id='PP_90007', unified_title='(PS4)')
    _blank_game(concept=stub)

    anchor = _run()

    stub.refresh_from_db()
    assert stub.unified_title == '(PS4)'
    anchor.assert_not_called()
    assert '[still untitled] PP_90007' in capsys.readouterr().out
