"""Tests for badge_candidates (badge_candidate_service + command).

A franchise or series is a candidate when one platform edition would GATE 2+ games with a LIVE contract. A game
is an origin game (a cross-gen pair, port or remaster folds into its original, as in a stage), it counts on an
edition only where the engine would let it gate, membership matches what `generate_badge_stages` would build,
and candidates rank by the average game-page players of their contracted games. Subjects that already have a
badge are hidden unless asked for (a franchise or series by any badge naming it).
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import ConceptFranchise, Contract, ContractBundle, Franchise, GameFamily
from trophies.services.badge_candidate_service import badge_candidates, existing_badge
from tests.factories import BadgeSeriesFactory, CompanyFactory, ConceptFactory, GameFactory, IGDBMatchFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('production_editions')]


_ids = iter(range(90_000, 99_999))


def _subject(name, source_type='franchise'):
    return Franchise.objects.create(
        igdb_id=next(_ids), name=name, slug=f"{name.lower().replace(' ', '-')}-{source_type}",
        source_type=source_type,
    )


def _game(subject, platforms=('PS5',), players=0, igdb_id=None, live=True, anchored=True,
          status='auto_accepted', family=None, delisted=False, obtainable=True, **link):
    """A concept in `subject` whose IGDB id keys a contract. Pass an existing igdb_id for a cross-gen sibling,
    or a shared family for a remaster on its own IGDB id."""
    concept = ConceptFactory(
        page_player_count=players, anchor_migration_completed_at=timezone.now() if anchored else None,
        family=family,
    )
    igdb_id = igdb_id or next(_ids)
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id, status=status)
    GameFactory(concept=concept, title_platform=list(platforms), is_delisted=delisted, is_obtainable=obtainable)
    Contract.objects.get_or_create(
        igdb_id=igdb_id, defaults={'name': f"Contract {igdb_id}", 'slug': f"contract-{igdb_id}", 'is_live': live},
    )
    ConceptFranchise.objects.create(concept=concept, franchise=subject, **link)
    return concept


def _by_name():
    return {c.subject.name: c for c in badge_candidates()}


# --- the 2+ games on one edition rule --------------------------------------------


def test_two_contracted_games_on_one_edition_qualify():
    subject = _subject("Uncharted")
    _game(subject, platforms=['PS4'])
    _game(subject, platforms=['PS5'])
    _game(subject, platforms=['PS3'])

    candidate = _by_name()["Uncharted"]
    assert candidate.edition_counts == {'ultra-hd': 2, 'legacy-hd': 1}
    assert candidate.game_count == 3


def test_one_game_per_edition_does_not_qualify():
    subject = _subject("Split")
    _game(subject, platforms=['PS3'])
    _game(subject, platforms=['PS5'])

    assert "Split" not in _by_name()


def test_a_cross_gen_pair_is_one_game():
    """Two concepts on one IGDB id are one contract, so one game, as they would be one stage."""
    subject = _subject("Cross Gen")
    first = _game(subject, platforms=['PS4'])
    _game(subject, platforms=['PS5'], igdb_id=first.igdb_match.igdb_id)

    assert "Cross Gen" not in _by_name()


def test_a_remaster_on_its_own_igdb_id_folds_into_its_original():
    """Two contracts, but one family, so one stage: not two games."""
    subject = _subject("Remastered")
    family = GameFamily.objects.create(canonical_name="Original", igdb_id=next(_ids))
    _game(subject, platforms=['PS4'], family=family, players=100)
    _game(subject, platforms=['PS5'], family=family, players=700)

    assert "Remastered" not in _by_name()

    _game(subject, platforms=['PS5'], players=300)
    candidate = _by_name()["Remastered"]
    assert (candidate.edition_counts, candidate.game_count) == ({'ultra-hd': 2}, 2)
    assert candidate.avg_players == 500   # the family takes its most-played version, once


def test_a_delisted_game_does_not_gate_where_the_edition_excludes_delisted():
    """Ultra HD excludes delisted games from gating; Legacy HD does not."""
    subject = _subject("Delisted")
    _game(subject, platforms=['PS4'])
    _game(subject, platforms=['PS4'], delisted=True)
    assert "Delisted" not in _by_name()

    legacy = _subject("Delisted Legacy")
    _game(legacy, platforms=['PS3'])
    _game(legacy, platforms=['PS3'], delisted=True)
    assert _by_name()["Delisted Legacy"].edition_counts == {'legacy-hd': 2}


def test_an_unobtainable_game_does_not_gate():
    subject = _subject("Unobtainable")
    _game(subject, platforms=['PS4'])
    _game(subject, platforms=['PS4'], obtainable=False)

    assert "Unobtainable" not in _by_name()


def test_a_game_on_both_editions_counts_on_each():
    subject = _subject("Both")
    _game(subject, platforms=['PS3', 'PS4'])
    _game(subject, platforms=['PSVITA', 'PS5'])

    assert _by_name()["Both"].edition_counts == {'legacy-hd': 2, 'ultra-hd': 2}


# --- which contracts count -------------------------------------------------------


def test_a_staged_contract_does_not_count():
    subject = _subject("Staged")
    _game(subject)
    _game(subject, live=False)

    assert "Staged" not in _by_name()


@pytest.mark.parametrize('kwargs', [{'anchored': False}, {'status': 'pending_review'}])
def test_an_unanchored_or_untrusted_concept_has_no_contract(kwargs):
    subject = _subject("Unresolved")
    _game(subject)
    _game(subject, **kwargs)

    assert "Unresolved" not in _by_name()


def _season(subject, episodes, players=0):
    """A bundle-only (episodic) contract: no igdb id, its episodes are unfamilied concepts in its bundle."""
    contract = Contract.objects.create(name=f"Season {subject.pk}", slug=f"season-{subject.pk}", is_live=True)
    bundle = ContractBundle.objects.create(contract=contract, label="Season")
    for _ in range(episodes):
        episode = ConceptFactory(page_player_count=players)
        IGDBMatchFactory(concept=episode, igdb_id=next(_ids))   # each episode on its own IGDB entry
        GameFactory(concept=episode, title_platform=['PS4'])
        bundle.concepts.add(episode)
        ConceptFranchise.objects.create(concept=episode, franchise=subject)


def test_a_bundle_only_contract_counts():
    subject = _subject("Episodic")
    _game(subject)
    _season(subject, episodes=1)

    assert _by_name()["Episodic"].edition_counts == {'ultra-hd': 2}


def test_a_season_of_episodes_is_one_game():
    """Three episodes, one contract, one bundle stage: not three games."""
    subject = _subject("Season Only")
    _season(subject, episodes=3, players=90)
    assert "Season Only" not in _by_name()

    _game(subject, players=10)
    candidate = _by_name()["Season Only"]
    assert (candidate.edition_counts, candidate.game_count, candidate.avg_players) == ({'ultra-hd': 2}, 2, 50)


# --- membership matches generate_badge_stages ------------------------------------


def test_an_excluded_link_does_not_count():
    subject = _subject("Excluded")
    _game(subject)
    _game(subject, is_excluded=True)

    assert "Excluded" not in _by_name()


def test_a_series_spin_off_does_not_count():
    series = _subject("Saints Row", source_type='collection')
    _game(series)
    _game(series, is_spinoff=True)

    assert "Saints Row" not in _by_name()


def test_a_series_member_counts():
    series = _subject("Series Two", source_type='collection')
    _game(series)
    _game(series)

    assert _by_name()["Series Two"].subject.source_type == 'collection'


# --- ranking and players ---------------------------------------------------------


def test_ranked_by_average_players_per_game():
    small = _subject("Small")
    _game(small, players=100)
    _game(small, players=300)
    big = _subject("Big")
    _game(big, players=900)
    _game(big, players=1100)
    _game(big, platforms=['PS2'], players=0)   # on no edition, still a contracted game in the average

    candidates = badge_candidates()
    assert [c.subject.name for c in candidates] == ["Big", "Small"]
    assert candidates[0].avg_players == pytest.approx(2000 / 3)
    assert candidates[0].game_count == 3
    assert candidates[1].avg_players == 200


def test_a_cross_gen_pair_counts_its_players_once():
    subject = _subject("Pair")
    first = _game(subject, players=500)
    _game(subject, players=500, igdb_id=first.igdb_match.igdb_id, platforms=['PS4'])
    _game(subject, players=100)

    assert _by_name()["Pair"].avg_players == 300


# --- subjects that already have a badge ------------------------------------------


@pytest.mark.parametrize('field', ['franchise', 'collection'])
def test_a_subject_with_a_badge_is_hidden_unless_asked(field):
    subject = _subject("Has Badge", source_type='franchise' if field == 'franchise' else 'collection')
    _game(subject)
    _game(subject)
    BadgeSeriesFactory(series_slug="has-badge", **{field: subject})

    assert "Has Badge" not in _by_name()
    shown = {c.subject.name: c for c in badge_candidates(include_existing=True)}
    assert shown["Has Badge"].has_badge is True


def test_a_sister_badge_does_not_hide_the_other_subject():
    """A franchise badge exists; the same-named series is still a separate candidate."""
    franchise, series = _subject("God of War"), _subject("God of War", source_type='collection')
    for subject in (franchise, series):
        _game(subject)
        _game(subject)
    BadgeSeriesFactory(series_slug="god-of-war", franchise=franchise)

    assert [c.subject.source_type for c in badge_candidates()] == ['collection']


def test_a_series_badge_leaves_its_franchise_listed():
    """The other sister direction: a badge naming only the series does not take the franchise."""
    franchise, series = _subject("Uncharted"), _subject("Uncharted", source_type='collection')
    for subject in (franchise, series):
        _game(subject)
        _game(subject)
    BadgeSeriesFactory(series_slug="uncharted", collection=series)

    assert [c.subject.source_type for c in badge_candidates()] == ['franchise']


def test_a_developer_badge_naming_a_franchise_hides_it():
    franchise = _subject("Studio Franchise")
    _game(franchise)
    _game(franchise)
    BadgeSeriesFactory(series_slug="studio", badge_type='developer', developer=CompanyFactory(), franchise=franchise)

    assert "Studio Franchise" not in _by_name()


def test_a_badge_naming_both_hides_both():
    """The Last of Us: one badge names both the series and the franchise, so it covers both."""
    franchise = _subject("The Last of Us")
    series = _subject("The Last of Us", source_type='collection')
    for subject in (franchise, series):
        _game(subject)
        _game(subject)
    BadgeSeriesFactory(series_slug="the-last-of-us", franchise=franchise, collection=series)

    assert badge_candidates() == []
    assert {c.subject.source_type for c in badge_candidates(include_existing=True) if c.has_badge} == {
        'franchise', 'collection',
    }


def test_a_developer_badge_hides_no_franchise():
    franchise = _subject("Studio Hits")
    _game(franchise)
    _game(franchise)
    BadgeSeriesFactory(series_slug="studio", developer=CompanyFactory())

    assert "Studio Hits" in _by_name()


def test_existing_badge_needs_a_subject():
    """With nothing to match, filter(Q()) would return ANY badge, here a user badge."""
    BadgeSeriesFactory(series_slug="user-badge", badge_type='user')

    with pytest.raises(ValueError):
        existing_badge()


def test_nothing_contracted_is_empty():
    assert badge_candidates() == []


# --- command ---------------------------------------------------------------------


def _run(*args):
    out = StringIO()
    call_command('badge_candidates', *args, stdout=out)
    return out.getvalue()


def test_command_prints_a_row_per_candidate_with_its_slug():
    subject = _subject("Ratchet")
    _game(subject, players=1234)
    _game(subject, players=1234)

    output = _run()
    assert "Ultra HD" in output and "Legacy HD" in output
    row = next(line for line in output.splitlines() if "Ratchet" in line)
    assert "Franchise" in row and "1,234" in row and subject.slug in row


def test_command_limit_and_existing_marker():
    for name in ("One", "Two"):
        subject = _subject(name)
        _game(subject)
        _game(subject)
    BadgeSeriesFactory(series_slug="one", franchise=Franchise.objects.get(name="One"))

    assert "Showing 1 of 2." in _run('--limit', '1', '--include-existing')
    assert "2 candidate(s)." in _run('--limit', '0', '--include-existing')
    assert "One *" in _run('--include-existing')
    hidden = _run()
    assert "two-franchise" in hidden and "one-franchise" not in hidden


def test_command_with_no_candidates():
    assert "No franchise or series" in _run()
