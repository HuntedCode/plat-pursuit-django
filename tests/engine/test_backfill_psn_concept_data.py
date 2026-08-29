"""The deliberate walk that fills in PSN data for games the sync path will never revisit.

Capture only happens inside `_job_sync_title_id`, and that job is only queued for title_ids that did
not match a known game. So a game that is already anchored and already matched is never re-queued by
a normal profile sync and would never be captured. This command is the answer to that, and the tests
below pin the two properties that make it safe to point at a live catalogue: it enqueues on
bulk_priority (so it cannot starve real users), and --missing-only genuinely shrinks the work (so an
interrupted sweep resumes instead of re-spending PSN calls on rows it already has).
"""
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import ConceptFactory, GameFactory, ProfileFactory
from trophies.models import ScoutAccount
from trophies.services.psn_metadata_service import capture_psn_concept_data

pytestmark = pytest.mark.django_db


def _details(psn_id='12345'):
    return {'id': psn_id, 'nameEn': 'Ghost of Tsushima'}


@pytest.fixture
def driver():
    """An active scout, which is how the command resolves a driver with no --driver-profile."""
    profile = ProfileFactory(psn_username='sweepscout')
    ScoutAccount.objects.create(profile=profile, status='active')
    return profile


def _game(**over):
    fields = {'title_ids': ['CUSA00001_00'], 'title_platform': ['PS4']}
    fields.update(over)
    return GameFactory(**fields)


def test_jobs_land_on_bulk_priority_against_the_driver(driver):
    """bulk_priority is the whole reason this is safe to run against prod: it drains beneath every
    live user sync. A default-queue sweep would put a catalogue walk ahead of real people."""
    game = _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--yes')

    assign.assert_called_once_with(
        'sync_title_id',
        ['CUSA00001_00', game.np_communication_id],
        driver.id,
        priority_override='bulk_priority',
    )


def test_missing_only_skips_games_already_captured(driver):
    """The resume property. Without it, re-running after an interruption re-spends a PSN call on
    every game already done -- which for the full catalogue is the entire cost, twice."""
    captured_concept = ConceptFactory()
    capture_psn_concept_data(captured_concept, _details())
    _game(concept=captured_concept)

    uncaptured = _game(concept=ConceptFactory())

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--missing-only', '--yes')

    assert assign.call_count == 1
    assert assign.call_args.args[1][1] == uncaptured.np_communication_id


def test_missing_only_ignores_concept_less_games(driver):
    """A game with no concept has nothing to have captured, so it matches `psn_data__isnull=True`
    forever and would be swept on every single run without ever being satisfiable."""
    _game(concept=None)

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--missing-only', '--yes')

    assign.assert_not_called()


def test_a_game_with_no_title_id_is_never_enqueued(driver):
    """`title_ids[0]` is the job's first argument. An empty list would IndexError mid-sweep, after
    an arbitrary number of jobs had already been queued."""
    _game(title_ids=[])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--yes')

    assign.assert_not_called()


def test_a_pspc_game_resolves_to_the_console_platform_and_is_not_skipped(driver):
    """PSPC titles carry the real platform at [1]; sending 'PSPC' returns a sparse response that
    reads downstream as 'PSN has no data' rather than 'we asked the wrong question'."""
    _game(title_platform=['PSPC', 'PS5'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--yes')

    assert assign.call_count == 1


def test_a_pspc_game_with_nothing_behind_it_is_skipped(driver):
    _game(title_platform=['PSPC'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--yes')

    assign.assert_not_called()


def test_dry_run_queues_nothing(driver):
    """This walks the entire catalogue against a live PSN budget. Previewing must be free."""
    _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--dry-run')

    assign.assert_not_called()


def test_limit_caps_the_sweep(driver):
    for _ in range(3):
        _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--limit', '2', '--yes')

    assert assign.call_count == 2


def test_platform_filter_narrows_the_sweep(driver):
    _game(title_platform=['PS4'])
    ps5 = _game(title_platform=['PS5'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--platform', 'PS5', '--yes')

    assert assign.call_count == 1
    assert assign.call_args.args[1][1] == ps5.np_communication_id


def test_no_driver_available_is_a_clean_error_not_a_traceback():
    """No scout, no --driver-profile. Failing here costs nothing; failing halfway through a sweep
    leaves a partially-queued catalogue."""
    _game()

    with pytest.raises(CommandError):
        call_command('backfill_psn_concept_data', '--yes')


def test_an_unknown_driver_profile_is_rejected(driver):
    with pytest.raises(CommandError):
        call_command('backfill_psn_concept_data', '--driver-profile', 'nobody', '--yes')


# --- what the first pass left uncovered ---------------------------------------------------------

def test_the_sweep_refuses_to_run_with_capture_disabled(driver, settings):
    """The sharpest way to waste the entire catalogue budget: capture is behind a kill switch, and
    with it off every job still drains, still spends its PSN calls, and creates zero rows. The
    failure is silent AND invisible on retry -- --missing-only would find nothing captured and
    re-enqueue everything at full price."""
    settings.PSN_METADATA_CAPTURE_ENABLED = False
    _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        with pytest.raises(CommandError):
            call_command('backfill_psn_concept_data', '--yes')

    assign.assert_not_called()


def test_the_operator_is_warned_that_this_is_not_capture_only(driver):
    """sync_title_id is the full resolution pipeline, not a read-only capture. That warning is the
    only thing between an operator and an unexpected catalogue-wide write, and it was print-only
    with nothing asserting it -- deleting it left every test green."""
    import io
    _game()
    out = io.StringIO()

    with patch('trophies.psn_manager.PSNManager.assign_job'):
        call_command('backfill_psn_concept_data', '--yes', stdout=out)

    printed = out.getvalue()
    assert 'not a' in printed and 'read-only capture' in printed
    assert 'full concept-resolution pipeline' in printed


def test_declining_the_prompt_queues_nothing(driver):
    """A catalogue sweep has no undo: draining is the only way out and it costs the full budget."""
    _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        with patch('builtins.input', return_value='n'):
            call_command('backfill_psn_concept_data')

    assign.assert_not_called()


def test_confirming_the_prompt_proceeds(driver):
    _game()

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        with patch('builtins.input', return_value='y'):
            call_command('backfill_psn_concept_data')

    assert assign.call_count == 1


def test_only_the_first_title_id_is_swept_by_default(driver):
    """title_ids order is append-order, i.e. whichever the first syncing user owned -- arbitrary,
    not canonical. Pinned so the limitation is visible rather than assumed."""
    _game(title_ids=['CUSA00001_00', 'CUSA00002_00', 'CUSA00003_00'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--yes')

    assert assign.call_count == 1
    assert assign.call_args.args[1][0] == 'CUSA00001_00'


def test_all_title_ids_gathers_the_regional_storefronts(driver):
    """A JP title_id returns sparse from US/en-US and falls through to the Asian fallbacks, so it
    produces a JP-keyed row the US title_id never would. Without this flag a game with any row is
    filtered out by --missing-only and its other storefronts are never revisited."""
    _game(title_ids=['CUSA00001_00', 'CUSA00002_00', 'CUSA00003_00'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--all-title-ids', '--yes')

    assert assign.call_count == 3
    assert [c.args[1][0] for c in assign.call_args_list] == [
        'CUSA00001_00', 'CUSA00002_00', 'CUSA00003_00',
    ]


def test_blacklisted_title_ids_are_not_swept(driver):
    """The normal queue path filters these; sweeping by Game would re-run concept resolution on
    exactly the titles it was disabled for."""
    from trophies.util_modules.constants import TITLE_ID_BLACKLIST
    _game(title_ids=[TITLE_ID_BLACKLIST[0]])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--yes')

    assign.assert_not_called()


def test_a_concept_with_several_regional_rows_is_counted_once(driver):
    """The --missing-only join is multi-valued: `psn_data__isnull=False` on this data returns the
    same game three times. The chosen direction is null-extended and yields one row, but nothing
    pinned that, so a future flip to the positive form would silently triple-queue every game."""
    concept = ConceptFactory()
    for region, psn_id in (('US', '1'), ('JP', '2'), ('GB', '3')):
        capture_psn_concept_data(concept, _details(psn_id=psn_id), country=region)
    _game(concept=concept)
    uncaptured = _game(concept=ConceptFactory())

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--missing-only', '--yes')

    assert assign.call_count == 1
    assert assign.call_args.args[1][1] == uncaptured.np_communication_id


def test_the_queued_args_bind_to_the_workers_real_signature(driver):
    """Every other test asserts the args positionally against a literal, encoding the same
    assumption the command makes. This binds them to the ACTUAL parameter names of the worker
    method, so swapping the pair in the command cannot be 'fixed' by swapping the literal too.

    Read with `ast` rather than importing TokenKeeper: importing that module registers an atexit
    Redis cleanup hook, which a test has no business doing.
    """
    import ast
    import pathlib

    src = pathlib.Path('trophies/token_keeper.py').read_text(encoding='utf-8')
    params = next(
        [a.arg for a in n.args.args]
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == '_job_sync_title_id'
    )
    # def _job_sync_title_id(self, profile_id, title_id_str, np_communication_id), dispatched as
    # _job_sync_title_id(profile_id, args[0], args[1]) -- so args line up after self + profile_id.
    assert params[:2] == ['self', 'profile_id'], (
        f'the worker signature changed to {params}; this test and the command both assume '
        f'(self, profile_id, *args)'
    )
    arg_names = params[2:]

    game = _game(title_ids=['CUSA09999_00'])

    with patch('trophies.psn_manager.PSNManager.assign_job') as assign:
        call_command('backfill_psn_concept_data', '--yes')

    queued = dict(zip(arg_names, assign.call_args.args[1]))
    assert queued['title_id_str'] == 'CUSA09999_00'
    assert queued['np_communication_id'] == game.np_communication_id
