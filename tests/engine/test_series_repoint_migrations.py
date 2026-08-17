"""The two data migrations that move donation records off the legacy `Badge` FK (2026-08).

`fundraiser.0006` and `art_reveal.0004` each map a row to its `BadgeSeries` by slug, then drop the old FK.
Every claim row is a payment somebody made, so both migrations REFUSE rather than guess: an unmappable row
raises with the slug named instead of nulling the column or dropping the row.

That refusal is the part worth testing. A data migration that silently drops the awkward 5% looks
identical to one that works, right up until a donor asks where their credit went -- and by then the
original FK is gone and there is nothing left to reconstruct it from.

Both migrations therefore keep the decision in a PURE `plan_mapping(rows, series_by_slug)` and do the
writing separately. That split is what makes this testable at all: the migration's intermediate schema (a
nullable `series`) exists only between two operations, so it cannot be reconstructed from the finished
models -- but a pure function takes plain tuples and needs no database.
"""
import importlib

import pytest

fundraiser_mig = importlib.import_module('fundraiser.migrations.0006_claim_series_fk')
art_reveal_mig = importlib.import_module('art_reveal.migrations.0004_item_series_fk')

SERIES = {'souls': 10, 'capcom': 20}


# --- fundraiser: DonationBadgeClaim ------------------------------------------


def test_claims_map_by_slug():
    rows = [(1, 'souls'), (2, 'capcom')]
    assert fundraiser_mig.plan_mapping(rows, SERIES) == {1: 10, 2: 20}


def test_an_empty_table_maps_to_nothing():
    """A fresh install has no claims. Neither migration may require any to exist."""
    assert fundraiser_mig.plan_mapping([], SERIES) == {}


def test_a_slug_with_no_series_refuses_and_names_it():
    """The operator's fix is `convert_series_to_groups --all`, a documented deploy prerequisite. Failing
    is the point: a failed migration is recoverable, a nulled payment record is not."""
    with pytest.raises(RuntimeError) as exc:
        fundraiser_mig.plan_mapping([(1, 'souls'), (2, 'ghost-series')], SERIES)

    message = str(exc.value)
    assert 'ghost-series' in message, 'the error must name the offending slug'
    assert 'convert_series_to_groups' in message, 'the error must say how to fix it'
    assert 'payment' in message, 'the error must say why it refused rather than nulling'


def test_two_claims_on_one_series_refuse():
    """The target column is a OneToOne. Two claims mapping to one series cannot both survive, and picking
    a winner arbitrarily means silently deciding which donor keeps the credit."""
    with pytest.raises(RuntimeError) as exc:
        fundraiser_mig.plan_mapping([(1, 'souls'), (2, 'souls')], SERIES)

    assert 'souls' in str(exc.value)
    assert 'OneToOne' in str(exc.value)


def test_a_blank_or_null_slug_refuses_rather_than_silently_skipping():
    """Blank is the shape a legacy `Badge` with no series_slug leaves behind. Skipping it would look like
    success while quietly detaching a claim."""
    for bad in ('', '   ', None):
        with pytest.raises(RuntimeError):
            fundraiser_mig.plan_mapping([(1, bad)], SERIES)


def test_surrounding_whitespace_still_maps():
    """Denormalized text fields collect whitespace. Refusing over a stray space would be a false alarm
    that sends an operator hunting a data problem that is not there."""
    assert fundraiser_mig.plan_mapping([(1, '  souls  ')], SERIES) == {1: 10}


# --- art_reveal: ArtRevealItem -----------------------------------------------


def test_items_map_by_slug():
    rows = [(1, 100, 'souls'), (2, 100, 'capcom')]
    assert art_reveal_mig.plan_mapping(rows, SERIES) == {1: 10, 2: 20}


def test_the_same_series_may_appear_in_two_different_events():
    """Uniqueness is per (event, series), not per series. A series revealed in a spring event and again
    in a winter one is legitimate, and refusing it would block a valid migration."""
    rows = [(1, 100, 'souls'), (2, 200, 'souls')]
    assert art_reveal_mig.plan_mapping(rows, SERIES) == {1: 10, 2: 10}


def test_two_items_on_one_series_in_one_event_refuse():
    with pytest.raises(RuntimeError) as exc:
        art_reveal_mig.plan_mapping([(1, 100, 'souls'), (2, 100, 'souls')], SERIES)

    assert 'souls' in str(exc.value)
    assert 'event 100' in str(exc.value), 'the error must name the event, since uniqueness is per-event'


def test_an_unmappable_item_refuses_and_names_it():
    with pytest.raises(RuntimeError) as exc:
        art_reveal_mig.plan_mapping([(1, 100, 'ghost-series')], SERIES)

    assert 'ghost-series' in str(exc.value)
    assert 'convert_series_to_groups' in str(exc.value)


def test_nothing_is_planned_when_any_row_fails():
    """Plan-then-write: a refusal must produce NO mapping at all, not a partial one. An earlier draft
    updated rows inside the loop and raised at the end, so a refusal still left writes behind -- only
    survivable because RunPython happens to be transactional."""
    with pytest.raises(RuntimeError):
        art_reveal_mig.plan_mapping([(1, 100, 'souls'), (2, 100, 'ghost')], SERIES)
    # The good row was never written anywhere, because planning raised before any write could happen.
