"""Tests for build_badge: one command makes the BadgeSeries, runs generate_badge_stages, then
convert_series_to_groups, all in one transaction.

The type follows the flags (series alone or with a franchise -> 'series', franchise alone -> 'franchise',
developer -> 'developer'), the name and slug come from the subject (the series when both are given), and every
edition is created hidden. Any failure, and every --dry-run, leaves nothing behind.
"""

import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from trophies.models import BadgeSeries, ConceptFranchise, Franchise, GroupBadge, Stage
from tests.factories import (
    BadgeSeriesFactory, CompanyFactory, ConceptCompanyFactory, ConceptFactory, GameFactory, IGDBMatchFactory,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('production_editions')]


_ids = iter(range(80_000, 89_999))


def _subject(name, slug, source_type='franchise'):
    return Franchise.objects.create(igdb_id=next(_ids), name=name, slug=slug, source_type=source_type)


def _game(title, platforms=('PS5',), subjects=(), developer=None):
    """A concept with its own IGDB id (so its own stage), on `platforms`, linked to each subject."""
    concept = ConceptFactory(unified_title=title)
    IGDBMatchFactory(concept=concept, igdb_id=next(_ids), igdb_name=title)
    GameFactory(concept=concept, title_platform=list(platforms))
    for subject in subjects:
        ConceptFranchise.objects.create(concept=concept, franchise=subject)
    if developer is not None:
        ConceptCompanyFactory(concept=concept, company=developer, is_developer=True)
    return concept


def _run(*args):
    out = StringIO()
    call_command('build_badge', *args, stdout=out)
    return out.getvalue()


def _editions(series):
    return {(gb.platform_group.key, gb.is_live) for gb in series.group_badges.select_related('platform_group')}


def _stage_concepts(slug):
    return {c.pk for s in Stage.objects.filter(series_slug=slug) for c in s.concepts.all()}


# --- what gets built --------------------------------------------------------------


def test_franchise_builds_series_stages_and_hidden_editions():
    franchise = _subject("Killzone", "killzone")
    games = [_game("Killzone 2", ['PS3'], [franchise]), _game("Shadow Fall", ['PS4'], [franchise])]

    output = _run('--franchise', 'killzone')

    series = BadgeSeries.objects.get(series_slug='killzone')
    assert (series.name, series.badge_type, series.completion_policy) == ("Killzone", 'franchise', 'all')
    assert (series.franchise, series.collection, series.developer) == (franchise, None, None)
    assert _stage_concepts('killzone') == {g.pk for g in games}
    assert _editions(series) == {('legacy-hd', False), ('ultra-hd', False)}
    assert 'Built "Killzone" (killzone, Franchise): 2 stage(s), hidden editions: Ultra HD, Legacy HD.' in output
    # The stage generator's own "Next: convert_series_to_groups" is silenced: this command just ran it.
    assert "python manage.py convert_series_to_groups" not in output
    assert output.count("Next:") == 1 and "evaluate_badges --series killzone" in output


def test_editions_only_for_platform_groups_the_stages_reach():
    franchise = _subject("Modern", "modern")
    _game("One", ['PS5'], [franchise])
    _game("Two", ['PS4'], [franchise])

    _run('--franchise', 'modern')

    assert _editions(BadgeSeries.objects.get(series_slug='modern')) == {('ultra-hd', False)}


def test_series_alone_is_a_series_badge():
    series_subject = _subject("Sly Cooper", "sly-cooper", 'collection')
    _game("Sly 1", ['PS3'], [series_subject])

    _run('--series', 'sly-cooper')

    series = BadgeSeries.objects.get(series_slug='sly-cooper')
    assert (series.badge_type, series.collection, series.franchise) == ('series', series_subject, None)


def test_both_is_a_series_badge_named_after_the_series():
    franchise = _subject("God of War", "god-of-war")
    collection = _subject("God of War (Norse)", "god-of-war-norse", 'collection')
    in_both = _game("God of War (2018)", ['PS4'], [franchise, collection])
    franchise_only = _game("Ascension", ['PS3'], [franchise])

    _run('--franchise', 'god-of-war', '--series', 'god-of-war-norse')

    series = BadgeSeries.objects.get(series_slug='god-of-war-norse')
    assert (series.name, series.badge_type) == ("God of War (Norse)", 'series')
    assert (series.franchise, series.collection) == (franchise, collection)
    # Stages cover both subjects, as generate_badge_stages unions them.
    assert _stage_concepts('god-of-war-norse') == {in_both.pk, franchise_only.pk}


def test_developer_badge():
    studio = CompanyFactory(name="Insomniac Games", slug="insomniac-games")
    game = _game("Resistance", ['PS3'], developer=studio)

    _run('--developer', 'insomniac-games')

    series = BadgeSeries.objects.get(series_slug='insomniac-games')
    assert (series.name, series.badge_type, series.developer) == ("Insomniac Games", 'developer', studio)
    assert _stage_concepts('insomniac-games') == {game.pk}


def test_slug_override_is_slugified():
    franchise = _subject("Jak", "jak-and-daxter-1234")
    _game("Jak", ['PS4'], [franchise])

    _run('--franchise', 'jak-and-daxter-1234', '--slug', 'Jak And Daxter')

    assert BadgeSeries.objects.filter(series_slug='jak-and-daxter', name="Jak").exists()


def test_dry_run_prints_the_build_and_saves_nothing():
    franchise = _subject("Infamous", "infamous")
    _game("Second Son", ['PS4'], [franchise])

    output = _run('--franchise', 'infamous', '--dry-run')

    assert "Stage 1: Second Son" in output
    assert 'Dry run: "infamous" was built and rolled back.' in output
    assert not BadgeSeries.objects.filter(series_slug='infamous').exists()
    assert not Stage.objects.filter(series_slug='infamous').exists()
    assert not GroupBadge.objects.exists()


# --- refusals and rollback -------------------------------------------------------


@pytest.mark.parametrize('args, message', [
    ((), "Pass --franchise, --series (or both), or --developer."),
    (('--developer', 'x', '--franchise', 'y'), "--developer builds a developer badge on its own"),
    (('--franchise', 'nope'), 'No franchise with slug "nope".'),
    (('--developer', 'nope'), 'No company with slug "nope".'),
])
def test_bad_flags_are_refused(args, message):
    with pytest.raises(CommandError, match=re.escape(message)):
        _run(*args)


@pytest.mark.parametrize('source_type, flag, hint', [
    ('collection', '--franchise', "It is a series: pass it as --series."),
    ('franchise', '--series', "It is a franchise: pass it as --franchise."),
])
def test_wrong_kind_of_subject_says_which_flag_to_use(source_type, flag, hint):
    _subject("Spyro", "spyro", source_type)

    with pytest.raises(CommandError, match=re.escape(hint)):
        _run(flag, 'spyro')


def test_a_taken_slug_is_refused():
    _subject("Gran Turismo", "gran-turismo")
    BadgeSeriesFactory(series_slug='gran-turismo')

    with pytest.raises(CommandError, match='already uses the slug "gran-turismo"'):
        _run('--franchise', 'gran-turismo')


@pytest.mark.parametrize('badge_slug, args', [
    ('lbp', ('--slug', 'lbp-2')),
    # Its badge holds the very slug: say so, rather than "pass --slug", which would only lead back here.
    ('littlebigplanet', ()),
])
def test_a_subject_with_a_badge_is_refused(badge_slug, args):
    franchise = _subject("LittleBigPlanet", "littlebigplanet")
    BadgeSeriesFactory(series_slug=badge_slug, franchise=franchise)

    # Anchored: `--franchise` alone must not offer the drop-franchise hint.
    with pytest.raises(CommandError, match=re.escape(f'"{badge_slug}" already covers this franchise.') + '$'):
        _run('--franchise', 'littlebigplanet', *args)


def test_a_developer_with_a_badge_is_refused():
    studio = CompanyFactory(name="Naughty Dog", slug="naughty-dog")
    BadgeSeriesFactory(series_slug='nd', badge_type='developer', developer=studio)

    with pytest.raises(CommandError, match=re.escape('"nd" already covers this developer.') + '$'):
        _run('--developer', 'naughty-dog')


@pytest.mark.parametrize('existing, args, message', [
    # A badge naming both covers both: The Last of Us, asked for as either one.
    ({'franchise': True, 'collection': True}, ('--series', 'ico'), 'already covers this series.'),
    ({'franchise': True, 'collection': True}, ('--franchise', 'team-ico'), 'already covers this franchise.'),
    ({'franchise': True, 'collection': True}, ('--series', 'ico', '--franchise', 'team-ico'),
     'already covers this franchise and series.'),
    # A series-only badge covers the series however it is asked for again.
    ({'collection': True}, ('--series', 'ico', '--franchise', 'team-ico'), 'already covers this series.'),
    # A franchise badge covers every franchise game, so a series badge that also names the franchise repeats
    # it. The way out (a sister series badge) is named.
    ({'franchise': True}, ('--series', 'ico', '--franchise', 'team-ico'),
     'already covers this franchise. Drop --franchise to build a series badge beside it.'),
])
def test_a_subject_any_badge_names_is_refused(existing, args, message):
    franchise = _subject("Team Ico", "team-ico")
    series_subject = _subject("Ico", "ico", 'collection')
    BadgeSeriesFactory(series_slug='old-ico', franchise=franchise if existing.get('franchise') else None,
                       collection=series_subject if existing.get('collection') else None)

    with pytest.raises(CommandError, match=re.escape(f'"old-ico" {message}') + '$'):
        _run(*args, '--slug', 'new-ico')


def test_no_drop_franchise_hint_when_another_badge_holds_the_series():
    """Two badges, one each: dropping --franchise would only hit the series badge, so the hint is withheld."""
    franchise = _subject("Team Ico", "team-ico")
    series_subject = _subject("Ico", "ico", 'collection')
    BadgeSeriesFactory(series_slug='franchise-badge', franchise=franchise)
    BadgeSeriesFactory(series_slug='series-badge', collection=series_subject)

    with pytest.raises(CommandError, match=re.escape('"franchise-badge" already covers this franchise.') + '$'):
        _run('--series', 'ico', '--franchise', 'team-ico', '--slug', 'new-ico')


def test_a_franchise_with_only_a_series_badge_can_get_its_own():
    """The other sister direction: a badge naming only the series leaves the franchise free."""
    franchise = _subject("Souls", "souls")
    series_subject = _subject("Dark Souls", "dark-souls", 'collection')
    _game("Demon's Souls", ['PS3'], [franchise, series_subject])   # shared game: a real sister pair
    BadgeSeriesFactory(series_slug='dark-souls', collection=series_subject)

    _run('--franchise', 'souls')

    assert BadgeSeries.objects.get(series_slug='souls').badge_type == 'franchise'


def test_leftover_stages_on_the_slug_are_refused_up_front():
    _subject("Orphan", "orphan")
    Stage.objects.create(series_slug='orphan', stage_number=1, title="Hand made")

    with pytest.raises(CommandError, match='Stages already use the slug "orphan" with no badge.'):
        _run('--franchise', 'orphan')
    assert Stage.objects.filter(series_slug='orphan').count() == 1


def test_a_sister_series_badge_is_allowed():
    """A franchise badge exists; the same-named series is a different subject."""
    franchise = _subject("Ratchet", "ratchet")
    series_subject = _subject("Ratchet", "ratchet-series", 'collection')
    _game("Rift Apart", ['PS5'], [franchise, series_subject])
    BadgeSeriesFactory(series_slug='ratchet', franchise=franchise)

    _run('--series', 'ratchet-series')

    assert BadgeSeries.objects.get(series_slug='ratchet-series').badge_type == 'series'


def test_no_games_rolls_everything_back():
    _subject("Empty", "empty")

    with pytest.raises(CommandError, match='"Empty" has no games to build stages from. Nothing saved.'):
        _run('--franchise', 'empty')
    assert not BadgeSeries.objects.filter(series_slug='empty').exists()


def test_no_edition_rolls_the_stages_back_too():
    franchise = _subject("Retro", "retro")
    _game("PS2 Classic", ['PS2'], [franchise])

    with pytest.raises(CommandError, match='so "retro" has no edition. Nothing saved.'):
        _run('--franchise', 'retro')
    assert not BadgeSeries.objects.filter(series_slug='retro').exists()
    assert not Stage.objects.filter(series_slug='retro').exists()
