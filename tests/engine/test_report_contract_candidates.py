"""The media-density contract rule's calibration report (report_contract_candidates): the
tiers, the pyramid guard, and the two calibration read-outs. The rule itself is Jeffrey's
(video -> contract, 4+ shots -> review, else snooze) with the guard this suite pins: a
trailer alone must NOT auto-tier an easy-plat stack (the eastasiasoft case)."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory
from trophies.management.commands.report_contract_candidates import pyramid_is_degenerate
from trophies.models import Contract

pytestmark = pytest.mark.django_db

_REAL = {'bronze': 30, 'silver': 12, 'gold': 5, 'platinum': 1}
_STACK = {'bronze': 1, 'silver': 0, 'gold': 11, 'platinum': 1}   # the easy-plat signature


def _candidate(igdb_id, name, *, videos=0, shots=0, defined=_REAL, shovelware=''):
    concept = ConceptFactory(unified_title=name)
    concept.anchor_migration_completed_at = timezone.now()
    concept.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(
        concept=concept, igdb_id=igdb_id, igdb_name=name,
        igdb_video_youtube_ids=[f'v{i}' for i in range(videos)],
        igdb_screenshot_image_ids=[f's{i}' for i in range(shots)],
    )
    GameFactory(concept=concept, title_name=name, defined_trophies=defined,
                **({'shovelware_status': shovelware} if shovelware else {}))
    return concept


def _run(**opts):
    out = StringIO()
    call_command('report_contract_candidates', stdout=out, **opts)
    return out.getvalue()


def test_pyramid_signature():
    """The guard's two prongs: gold-heavy OR tiny earnable list = degenerate."""
    assert pyramid_is_degenerate(_STACK)
    assert pyramid_is_degenerate({'bronze': 3, 'silver': 2, 'gold': 1, 'platinum': 1})  # tiny
    assert not pyramid_is_degenerate(_REAL)
    assert pyramid_is_degenerate({})   # no data reads degenerate, never auto-tier


def test_tiers_and_the_eastasiasoft_guard():
    """Video + real pyramid -> A. Video + easy-plat stack -> B (the guard, THE point).
    No video + 4 shots -> B. Thin everything -> C."""
    _candidate(91001, 'Real AA Game', videos=1, shots=6)
    _candidate(91002, 'Pretty Girls Stackitaire', videos=1, shots=6, defined=_STACK)
    _candidate(91003, 'Quiet Indie', shots=4)
    _candidate(91004, 'Bare Page', shots=2)

    out = _run()

    a = out.split('Tier A')[1].split('Tier B')[0]
    b = out.split('Tier B')[1].split('Tier C')[0]
    c = out.split('Tier C')[1]
    assert 'Real AA Game' in a and 'Pretty Girls Stackitaire' not in a
    assert 'Pretty Girls Stackitaire' in b and 'Quiet Indie' in b
    assert 'Bare Page' in c


def test_guard_readout_counts_shovelware_delta():
    """The precision read-out: without the guard, every video game (shovelware included) lands
    in the auto tier; with it, the flagged stack falls to review."""
    _candidate(91011, 'Legit Trailer Game', videos=1)
    _candidate(91012, 'Flagged Stack', videos=1, defined=_STACK, shovelware='auto_flagged')

    out = _run()

    assert 'Without guard: video => contract would auto-admit 2 games, 1 flagged shovelware' in out
    assert 'With guard:    Tier A admits 1 games, 0 flagged shovelware' in out


def test_contracted_games_feed_recall_not_the_population():
    """Existing contracts are the ground truth: a contracted IGDB id is excluded from the
    scoring population and lands in the recall read-out instead."""
    _candidate(91021, 'Already Contracted', videos=1)
    Contract.objects.create(name='Held', slug='held', igdb_id=91021, is_live=True)
    _candidate(91022, 'Not Yet', videos=1)

    out = _run()

    assert 'Population: 1 uncontracted IGDB games' in out
    assert 'Rule would have surfaced 1/1 (100.0%)' in out
    assert 'Already Contracted' not in out.split('Tier A')[1].split('The pyramid guard')[0]


def test_min_shots_knob():
    """--min-shots moves the B/C boundary (the calibration knob the report exists to tune)."""
    _candidate(91031, 'Three Shots', shots=3)

    assert 'Three Shots' in _run().split('Tier C')[1]                    # default 4: snoozed
    assert 'Three Shots' in _run(min_shots=3).split('Tier B')[1].split('Tier C')[0]


def test_sibling_concepts_vote_once():
    """Two trusted concepts sharing one IGDB page are ONE game in the population."""
    a = _candidate(91041, 'Split Sibling A', videos=1)
    b = ConceptFactory(unified_title='Split Sibling B')
    b.anchor_migration_completed_at = timezone.now()
    b.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(concept=b, igdb_id=91041, igdb_name='Split Sibling B',
                     igdb_video_youtube_ids=['v1'])
    GameFactory(concept=b, defined_trophies=_REAL)

    out = _run()

    assert 'Population: 1 uncontracted IGDB games' in out
