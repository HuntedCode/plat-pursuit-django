"""A Standalone Expansion (IGDB game_type 4) is its own game, never a version of its parent.

Owner's call (2026-10): Dishonored: Death of the Outsider points at Dishonored 2 through IGDB's `parent_game`,
and the family resolver used to follow that, so the two shared a GameFamily and `generate_badge_stages` (one
stage per family) made them one stage. The resolver now stops at a standalone expansion, for every badge type
and every page that groups by family. A remaster, port or edition OF an expansion still joins the expansion.

`relink_standalone_expansion_families` re-homes the families linked before the change.
"""
from datetime import datetime, timezone as dt_timezone
from io import StringIO

import pytest
from django.core.management import call_command

from tests.factories import BadgeSeriesFactory, ConceptFactory, GameFactory, IGDBMatchFactory, StageFactory
from trophies.models import ConceptFranchise, ConceptJoinReview, Franchise, GameFamily
from trophies.services.badge_stage_service import plan_stages
from trophies.services.igdb_service import IGDBService

pytestmark = pytest.mark.django_db

DISHONORED_2, DEATH_OF_THE_OUTSIDER, DOTO_REMASTER, DISHONORED_2_PORT = 20001, 20002, 20003, 20004


def _ts(year):
    return int(datetime(year, 6, 1, tzinfo=dt_timezone.utc).timestamp())


def _payload(igdb_id, name, game_type, year, *, parent_game=None, version_parent=None):
    """An IGDB game payload shaped like the API's (`game_type` and parents are expanded objects)."""
    data = {'id': igdb_id, 'name': name, 'game_type': {'id': game_type}, 'first_release_date': _ts(year)}
    if parent_game:
        data['parent_game'] = {'id': parent_game, 'name': f'#{parent_game}'}
    if version_parent:
        data['version_parent'] = {'id': version_parent, 'name': f'#{version_parent}'}
    return data


PAYLOADS = {
    DISHONORED_2: _payload(DISHONORED_2, 'Dishonored 2', 0, 2016),
    DEATH_OF_THE_OUTSIDER: _payload(DEATH_OF_THE_OUTSIDER, 'Dishonored: Death of the Outsider', 4, 2017,
                                    parent_game=DISHONORED_2),
    DOTO_REMASTER: _payload(DOTO_REMASTER, 'Death of the Outsider Remastered', 9, 2024,
                            parent_game=DEATH_OF_THE_OUTSIDER),
    DISHONORED_2_PORT: _payload(DISHONORED_2_PORT, 'Dishonored 2 (PS5)', 11, 2023, parent_game=DISHONORED_2),
}


@pytest.fixture
def igdb(monkeypatch):
    """Serve parent fetches from PAYLOADS and record which ids were fetched."""
    fetched = []

    def fetch(igdb_id):
        fetched.append(igdb_id)
        return PAYLOADS.get(igdb_id)
    monkeypatch.setattr(IGDBService, 'fetch_full_game_data', classmethod(lambda cls, igdb_id: fetch(igdb_id)))
    return fetched


def _matched(igdb_id, *, family=None):
    data = PAYLOADS[igdb_id]
    concept = ConceptFactory(unified_title=data['name'], family=family)
    match = IGDBMatchFactory(concept=concept, igdb_id=igdb_id, igdb_name=data['name'], raw_response=data,
                             game_category=data['game_type']['id'])
    return concept, match


# ── the resolver ─────────────────────────────────────────────────────────────────────────────────

def test_a_standalone_expansion_is_its_own_canonical_entry(igdb):
    assert IGDBService._resolve_canonical_igdb_id(PAYLOADS[DEATH_OF_THE_OUTSIDER], DEATH_OF_THE_OUTSIDER) \
        == DEATH_OF_THE_OUTSIDER
    assert igdb == [], 'the parent should never be fetched'


def test_a_remaster_of_a_standalone_expansion_stops_at_the_expansion(igdb):
    assert IGDBService._resolve_canonical_igdb_id(PAYLOADS[DOTO_REMASTER], DOTO_REMASTER) == DEATH_OF_THE_OUTSIDER
    assert igdb == [DEATH_OF_THE_OUTSIDER]


def test_an_edition_of_a_standalone_expansion_stops_at_the_expansion(igdb):
    edition = _payload(20005, 'Death of the Outsider Deluxe', 0, 2017, version_parent=DEATH_OF_THE_OUTSIDER)

    assert IGDBService._resolve_canonical_igdb_id(edition, 20005) == DEATH_OF_THE_OUTSIDER


def test_a_port_of_a_main_game_still_joins_the_original(igdb):
    # The rule is narrow: remakes, remasters, expanded games and ports still fold into their original.
    assert IGDBService._resolve_canonical_igdb_id(PAYLOADS[DISHONORED_2_PORT], DISHONORED_2_PORT) == DISHONORED_2


# ── linking + the relink command ─────────────────────────────────────────────────────────────────

def test_enrichment_links_a_standalone_expansion_into_its_own_named_family(igdb):
    concept, match = _matched(DEATH_OF_THE_OUTSIDER)

    IGDBService._link_concept_to_family(match, match.raw_response)

    concept.refresh_from_db()
    assert concept.family.igdb_id == DEATH_OF_THE_OUTSIDER
    assert concept.family.canonical_name == 'Dishonored: Death of the Outsider'
    assert concept.family.origin_first_release_date.year == 2017


def _old_layout():
    """The pre-fix state: the expansion and its remaster filed in Dishonored 2's family."""
    parent_family = GameFamily.objects.create(igdb_id=DISHONORED_2, canonical_name='Dishonored 2')
    base, _ = _matched(DISHONORED_2, family=parent_family)
    doto, _ = _matched(DEATH_OF_THE_OUTSIDER, family=parent_family)
    remaster, _ = _matched(DOTO_REMASTER, family=parent_family)
    return parent_family, base, doto, remaster


def _relink(*args):
    out = StringIO()
    call_command('relink_standalone_expansion_families', *args, stdout=out)
    return out.getvalue()


def test_relink_moves_the_expansion_and_its_remaster_out_of_the_parents_family(igdb):
    parent_family, base, doto, remaster = _old_layout()

    output = _relink()

    for concept in (base, doto, remaster):
        concept.refresh_from_db()
    assert base.family_id == parent_family.pk
    assert doto.family.igdb_id == DEATH_OF_THE_OUTSIDER
    assert remaster.family_id == doto.family_id
    assert 'Moved 2 concept(s)' in output


def test_relink_dry_run_writes_nothing_and_a_second_run_finds_nothing(igdb):
    parent_family, _, doto, _ = _old_layout()

    assert 'Would move 2 concept(s)' in _relink('--dry-run')
    doto.refresh_from_db()
    assert doto.family_id == parent_family.pk

    _relink()
    assert 'Moved 0 concept(s); 2 already in the right family.' in _relink()


def test_relink_deletes_a_parent_family_it_empties(igdb):
    # We hold the expansion but not its parent: the parent's family existed only for the expansion.
    parent_family = GameFamily.objects.create(igdb_id=DISHONORED_2, canonical_name='Dishonored 2')
    _matched(DEATH_OF_THE_OUTSIDER, family=parent_family)

    _relink()

    assert not GameFamily.objects.filter(pk=parent_family.pk).exists()


# ── the reason it matters: badge stages ──────────────────────────────────────────────────────────

def test_an_expansion_and_its_parent_generate_two_stages(igdb):
    franchise = Franchise.objects.create(igdb_id=1, name='Dishonored', slug='dishonored', source_type='franchise')
    series = BadgeSeriesFactory(series_slug='dishonored', badge_type='franchise', franchise=franchise)
    for igdb_id in (DEATH_OF_THE_OUTSIDER, DISHONORED_2):
        concept, match = _matched(igdb_id)
        IGDBService._link_concept_to_family(match, match.raw_response)
        ConceptFranchise.objects.create(concept=concept, franchise=franchise)

    assert [stage.title for stage in plan_stages(series)] == ['Dishonored 2', 'Dishonored: Death of the Outsider']


# ── what the families leave behind ───────────────────────────────────────────────────────────────

def _review(raw_id, canonical_id, status='pending'):
    return ConceptJoinReview.objects.create(
        game=GameFactory(), proposed_raw_igdb_id=raw_id, proposed_canonical_igdb_id=canonical_id, status=status,
    )


def test_relink_repoints_open_join_reviews_at_the_new_canonical(igdb, monkeypatch):
    _old_layout()
    # An expansion we hold no match for: its review is re-resolved from IGDB.
    monkeypatch.setitem(PAYLOADS, 20007, _payload(20007, 'Unheld Expansion', 4, 2018, parent_game=DISHONORED_2))
    pending = _review(DEATH_OF_THE_OUTSIDER, DISHONORED_2)
    deferred = _review(DOTO_REMASTER, DISHONORED_2, status='deferred')
    unheld = _review(20007, DISHONORED_2)
    closed = _review(DEATH_OF_THE_OUTSIDER, DISHONORED_2, status='approved')
    port = _review(DISHONORED_2_PORT, DISHONORED_2)   # a real derivative: re-resolved, and still Dishonored 2

    dry = _relink('--dry-run')
    pending.refresh_from_db()
    assert 'Would re-point 3 open join review(s)' in dry
    assert pending.proposed_canonical_igdb_id == DISHONORED_2

    _relink()

    for review in (pending, deferred, unheld, closed, port):
        review.refresh_from_db()
    assert [r.proposed_canonical_igdb_id for r in (pending, deferred, unheld)] == [
        DEATH_OF_THE_OUTSIDER, DEATH_OF_THE_OUTSIDER, 20007,
    ]
    assert closed.proposed_canonical_igdb_id == DISHONORED_2, 'a resolved review is history, never rewritten'
    assert port.proposed_canonical_igdb_id == DISHONORED_2


def test_relink_lists_stages_it_splits(igdb):
    _, base, doto, remaster = _old_layout()
    merged = StageFactory(series_slug='arkane', stage_number=3, title='Dishonored 2')
    merged.concepts.add(base, doto)
    # The expansion staged with its own remaster moves as a pair: still one game.
    pair = StageFactory(series_slug='arkane', stage_number=4, title='Death of the Outsider')
    pair.concepts.add(doto, remaster)

    dry = _relink('--dry-run')
    output = _relink()

    for text in (dry, output):
        assert 'split these by hand' in text
        assert 'arkane stage 3 ("Dishonored 2"): Dishonored: Death of the Outsider' in text
        assert 'arkane stage 4' not in text


def test_relink_survives_emptying_a_family_whose_expansion_is_staged(igdb):
    # We hold the expansion but not its parent: moving it deletes the parent's family. The stage check reads
    # the families captured before the move, so it neither crashes nor lists a stage that was not split.
    parent_family = GameFamily.objects.create(igdb_id=DISHONORED_2, canonical_name='Dishonored 2')
    doto, _ = _matched(DEATH_OF_THE_OUTSIDER, family=parent_family)
    StageFactory(series_slug='arkane-lone', stage_number=1).concepts.add(doto)

    output = _relink()

    assert 'Moved 1 concept(s)' in output and 'split these by hand' not in output
    assert not GameFamily.objects.filter(pk=parent_family.pk).exists()


def test_a_real_run_fetches_each_derivative_once(igdb):
    _old_layout()

    _relink()

    assert igdb == [DEATH_OF_THE_OUTSIDER], 'the remaster walks up to its expansion once; the expansion needs none'


def test_relink_reaches_an_edition_of_an_expansion_through_version_parent(igdb, monkeypatch):
    parent_family = GameFamily.objects.create(igdb_id=DISHONORED_2, canonical_name='Dishonored 2')
    _matched(DEATH_OF_THE_OUTSIDER, family=parent_family)
    monkeypatch.setitem(PAYLOADS, 20006, _payload(20006, 'Death of the Outsider Deluxe', 0, 2017,
                                                  version_parent=DEATH_OF_THE_OUTSIDER))
    edition, _ = _matched(20006, family=parent_family)

    _relink()

    edition.refresh_from_db()
    assert edition.family.igdb_id == DEATH_OF_THE_OUTSIDER


def _failing_fetch(monkeypatch, bad_id):
    """IGDB raises (a timeout, an HTTP error) for `bad_id`; every other id is served from PAYLOADS."""
    def fetch(cls, igdb_id):
        if igdb_id == bad_id:
            raise RuntimeError('IGDB timed out')
        return PAYLOADS.get(igdb_id)
    monkeypatch.setattr(IGDBService, 'fetch_full_game_data', classmethod(fetch))


def test_an_igdb_error_skips_that_game_but_keeps_the_stage_report(igdb, monkeypatch):
    # The remaster's walk up to its expansion fails; the expansion itself (no fetch needed) still moves, and
    # the stage its move split is still reported, because a re-run could not see that move again.
    _, base, doto, remaster = _old_layout()
    StageFactory(series_slug='arkane-err', stage_number=1, title='Dishonored 2').concepts.add(base, doto)
    _failing_fetch(monkeypatch, DEATH_OF_THE_OUTSIDER)

    output = _relink()

    doto.refresh_from_db()
    remaster.refresh_from_db()
    assert doto.family.igdb_id == DEATH_OF_THE_OUTSIDER
    assert remaster.family.igdb_id == DISHONORED_2, 'the failed game is left as it was'
    assert '1 game(s) failed' in output and 'Death of the Outsider Remastered: IGDB timed out' in output
    assert 'arkane-err stage 1 ("Dishonored 2")' in output


def test_a_review_igdb_cannot_resolve_is_listed_never_written(igdb, monkeypatch):
    _old_layout()
    review = _review(20008, DISHONORED_2)
    _failing_fetch(monkeypatch, 20008)

    output = _relink()

    review.refresh_from_db()
    assert review.proposed_canonical_igdb_id == DISHONORED_2
    assert f'could not be re-resolved from IGDB; check them by hand: {review.pk}' in output


def test_a_stage_that_already_spanned_two_families_is_listed_only_if_the_move_adds_one(igdb):
    _, base, doto, _ = _old_layout()
    elsewhere = ConceptFactory(family=GameFamily.objects.create(igdb_id=99999, canonical_name='Elsewhere'))
    grows = StageFactory(series_slug='mixed', stage_number=1, title='Grows')
    grows.concepts.add(base, doto, elsewhere)        # {D2, Elsewhere} -> {D2, DotO, Elsewhere}
    swaps = StageFactory(series_slug='mixed', stage_number=2, title='Swaps')
    swaps.concepts.add(doto, elsewhere)              # {D2, Elsewhere} -> {DotO, Elsewhere}: still two

    output = _relink()

    assert 'mixed stage 1 ("Grows")' in output
    assert 'mixed stage 2' not in output


def test_dry_and_real_runs_agree_on_an_expansion_that_had_no_family(igdb):
    parent_family = GameFamily.objects.create(igdb_id=DISHONORED_2, canonical_name='Dishonored 2')
    base, _ = _matched(DISHONORED_2, family=parent_family)
    doto, _ = _matched(DEATH_OF_THE_OUTSIDER)        # never linked: no family at all
    StageFactory(series_slug='nofam', stage_number=1, title='Dishonored 2').concepts.add(base, doto)

    dry = _relink('--dry-run')
    real = _relink()

    for text in (dry, real):
        assert 'nofam stage 1 ("Dishonored 2"): Dishonored: Death of the Outsider' in text


def test_a_review_whose_walk_breaks_partway_is_listed_never_written(igdb, monkeypatch):
    # The review's own version fetches fine, but its parent does not (IGDB returns nothing for 20010): the
    # resolver then stops on the parent's bare id, which is not a canonical anyone verified.
    _old_layout()
    monkeypatch.setitem(PAYLOADS, 20009, _payload(20009, 'Port of an unheld game', 11, 2020, parent_game=20010))
    review = _review(20009, DISHONORED_2)

    output = _relink()

    review.refresh_from_db()
    assert review.proposed_canonical_igdb_id == DISHONORED_2
    assert f'check them by hand: {review.pk}' in output
