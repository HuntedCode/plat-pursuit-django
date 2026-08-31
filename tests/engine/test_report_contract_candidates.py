"""The media-density contract rule's calibration report (report_contract_candidates, v2): the
tiers, the pyramid guard, the shovelware override (flagged games NEVER auto-accept -- Jeffrey's
rule after the first prod run), the franchise rescue (real IP with a thin old IGDB page is
review, not snooze), demand-ranked samples, and the calibration read-outs."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory
from trophies.management.commands.report_contract_candidates import pyramid_is_degenerate
from trophies.models import ConceptFranchise, Contract, Franchise

pytestmark = pytest.mark.django_db

_REAL = {'bronze': 30, 'silver': 12, 'gold': 5, 'platinum': 1}
_STACK = {'bronze': 1, 'silver': 0, 'gold': 11, 'platinum': 1}   # the easy-plat signature


def _candidate(igdb_id, name, *, videos=0, shots=0, defined=_REAL, shovelware='', played=0,
               franchise=None):
    concept = ConceptFactory(unified_title=name)
    concept.anchor_migration_completed_at = timezone.now()
    concept.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(
        concept=concept, igdb_id=igdb_id, igdb_name=name,
        igdb_video_youtube_ids=[f'v{i}' for i in range(videos)],
        igdb_screenshot_image_ids=[f's{i}' for i in range(shots)],
    )
    GameFactory(concept=concept, title_name=name, defined_trophies=defined, played_count=played,
                **({'shovelware_status': shovelware} if shovelware else {}))
    if franchise is not None:
        ConceptFranchise.objects.create(concept=concept, franchise=franchise)
    return concept


def _franchise(igdb_id=701, name='Big IP'):
    return Franchise.objects.create(igdb_id=igdb_id, name=name, slug=name.lower().replace(' ', '-'),
                                    source_type='franchise')


def _run(**opts):
    out = StringIO()
    call_command('report_contract_candidates', stdout=out, **opts)
    return out.getvalue()


def _tier(out, t):
    parts = {'A': ('Tier A', 'Tier B'), 'B': ('Tier B', 'Tier C'),
             'C': ('Tier C', 'The precision ladder')}
    start, end = parts[t]
    return out.split(start)[1].split(end)[0]


def test_pyramid_signature():
    """The guard's two prongs: gold-heavy OR tiny earnable list = degenerate."""
    assert pyramid_is_degenerate(_STACK)
    assert pyramid_is_degenerate({'bronze': 3, 'silver': 2, 'gold': 1, 'platinum': 1})  # tiny
    assert not pyramid_is_degenerate(_REAL)
    assert pyramid_is_degenerate({})   # no data reads degenerate, never auto-tier


def test_tiers_guard_and_v2_rules():
    """Video + real pyramid -> A. Video + easy-plat stack -> B (the pyramid guard).
    Video + real pyramid + SHOVELWARE FLAG -> B, blocked (Jeffrey's override: flagged games
    never auto-accept). Thin page + franchise membership -> B, rescued (the AAA back-catalog
    fix). Thin page, no franchise -> C."""
    ip = _franchise()
    _candidate(91001, 'Real AA Game', videos=1, shots=6)
    _candidate(91002, 'Pretty Girls Stackitaire', videos=1, shots=6, defined=_STACK)
    _candidate(91003, 'Flagged But Polished', videos=1, shots=6, shovelware='manually_flagged')
    _candidate(91004, 'Old AAA Classic', shots=2, franchise=ip)
    _candidate(91005, 'Bare Page', shots=2)

    out = _run()
    a, b, c = _tier(out, 'A'), _tier(out, 'B'), _tier(out, 'C')

    assert 'Real AA Game' in a
    assert 'Pretty Girls Stackitaire' in b and 'Pretty Girls Stackitaire' not in a
    assert 'Flagged But Polished' in b and 'Flagged But Polished' not in a
    assert 'Old AAA Classic' in b and 'Old AAA Classic' not in c
    assert 'Bare Page' in c
    # The override and the rescue are each named with their haul.
    assert 'the override sent 1 to review' in out
    assert 'Promoted to review: 1 games' in out
    assert 'Flagged But Polished' in out.split('Blocked from auto')[1].split('franchise rescue')[0]
    assert 'Old AAA Classic' in out.split('The franchise rescue')[1].split('Recall')[0]


def test_precision_ladder_readout():
    """The three-step ladder: no guard admits everything with a video; the pyramid guard drops
    the degenerate stacks; the shovelware block empties the flagged remainder out of A."""
    _candidate(91011, 'Legit Trailer Game', videos=1)
    _candidate(91012, 'Flagged Stack', videos=1, defined=_STACK, shovelware='auto_flagged')
    _candidate(91013, 'Flagged Polished', videos=1, shovelware='auto_flagged')

    out = _run()

    assert 'No guard:            video => contract would auto-admit 3 games, 2 flagged shovelware' in out
    assert '+ pyramid guard:     2 would remain, 1 flagged shovelware' in out
    assert '+ shovelware block:  Tier A admits 1 games, 0 flagged shovelware (the override sent 1 to review)' in out


def test_rescue_honesty_check():
    """The rescue's shovelware haul is reported: a flagged game in a franchise still gets
    promoted (review sees it), but the read-out counts it."""
    ip = _franchise()
    _candidate(91021, 'Franchise Junk', shots=1, defined=_STACK, shovelware='auto_flagged',
               franchise=ip)

    out = _run()

    assert 'Promoted to review: 1 games (1 of them flagged shovelware -- the honesty check)' in out


def test_samples_rank_by_players_and_min_players_floor():
    """Queues are demand-ranked (top played first -- contracts exist where players are), and
    --min-players drops sub-floor games from the population entirely."""
    _candidate(91031, 'Popular Game', videos=1, played=5000)
    _candidate(91032, 'Quiet Game', videos=1, played=3)

    out = _run()
    a = _tier(out, 'A')
    assert a.index('Popular Game') < a.index('Quiet Game')
    assert '(5000 players)' in a

    floored = _run(min_players=100)
    assert 'Quiet Game' not in floored
    assert '1 below the demand floor' in floored
    assert 'Population: 1 uncontracted IGDB games' in floored


def test_contracted_games_feed_recall_not_the_population():
    """Existing contracts are the ground truth: a contracted IGDB id is excluded from the
    scoring population and lands in the recall read-out instead."""
    _candidate(91041, 'Already Contracted', videos=1)
    Contract.objects.create(name='Held', slug='held', igdb_id=91041, is_live=True)
    _candidate(91042, 'Not Yet', videos=1)

    out = _run()

    assert 'Population: 1 uncontracted IGDB games' in out
    assert 'Rule would have surfaced 1/1 (100.0%)' in out
    assert 'Already Contracted' not in _tier(out, 'A')


def test_min_shots_knob():
    """--min-shots moves the B/C boundary (the calibration knob the report exists to tune)."""
    _candidate(91051, 'Three Shots', shots=3)

    assert 'Three Shots' in _tier(_run(), 'C')                    # default 4: snoozed
    assert 'Three Shots' in _tier(_run(min_shots=3), 'B')


def test_sibling_concepts_vote_once():
    """Two trusted concepts sharing one IGDB page are ONE game in the population."""
    _candidate(91061, 'Split Sibling A', videos=1)
    b = ConceptFactory(unified_title='Split Sibling B')
    b.anchor_migration_completed_at = timezone.now()
    b.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(concept=b, igdb_id=91061, igdb_name='Split Sibling B',
                     igdb_video_youtube_ids=['v1'])
    GameFactory(concept=b, defined_trophies=_REAL)

    out = _run()

    assert 'Population: 1 uncontracted IGDB games' in out


def test_excluded_franchise_link_does_not_rescue():
    """The rescue honors is_excluded: a staff-hidden link proves nothing."""
    ip = _franchise()
    c = _candidate(91071, 'Bad Link Game', shots=1)
    ConceptFranchise.objects.filter(concept=c).delete()
    ConceptFranchise.objects.create(concept=c, franchise=ip, is_excluded=True)

    assert 'Bad Link Game' in _tier(_run(), 'C')
