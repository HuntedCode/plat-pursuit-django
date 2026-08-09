"""Plat card data layer + endpoints.

A card is earned by finishing a game's DEFAULT trophy group -- platinum or not. That rule is the whole
point of the 2026-08 rebuild: the old anchor was a platinum EarnedTrophy, which silently excluded every
100%-with-no-platinum game.
"""
import pytest
from django.utils import timezone

from core.services import completion_card_service as cards
from trophies.models import Title, UserTitle
from tests.factories import (
    BadgeSeriesFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory,
    ProfileGameFactory, ProfileTrophyGroupFactory, StageFactory, TrophyFactory, TrophyGroupFactory,
    EarnedTrophyFactory,
)

pytestmark = pytest.mark.django_db


def _completed_game(profile, *, with_platinum, progress=100, full_game_progress=0, name=None):
    """A game whose default group this profile has finished."""
    defined = {'bronze': 10, 'silver': 4, 'gold': 2, 'platinum': 1 if with_platinum else 0}
    game = GameFactory(defined_trophies=defined, **({'title_name': name} if name else {}))
    group = TrophyGroupFactory(game=game, trophy_group_id='default', defined_trophies=defined)
    ProfileGameFactory(profile=profile, game=game, progress=full_game_progress, has_plat=with_platinum)
    standing = ProfileTrophyGroupFactory(profile=profile, trophy_group=group, progress=progress)
    if with_platinum:
        trophy = TrophyFactory(game=game, trophy_type='platinum', trophy_group_id='default')
        EarnedTrophyFactory(profile=profile, trophy=trophy, earned=True)
    return game, group, standing


# ── Eligibility ───────────────────────────────────────────────────────────────────────────────────

def test_a_100_percent_game_with_no_platinum_earns_a_card():
    """The entire reason for the rebuild: this game could not produce a card before."""
    profile = ProfileFactory()
    _, group, _ = _completed_game(profile, with_platinum=False)

    assert [c.trophy_group_id for c in cards.eligible_completions(profile)] == [group.id]


def test_a_platinum_still_earns_a_card():
    profile = ProfileFactory()
    _, group, _ = _completed_game(profile, with_platinum=True)

    assert [c.trophy_group_id for c in cards.eligible_completions(profile)] == [group.id]


def test_an_unfinished_default_group_earns_nothing():
    profile = ProfileFactory()
    _completed_game(profile, with_platinum=True, progress=94)

    assert list(cards.eligible_completions(profile)) == []


def test_outstanding_dlc_does_not_block_a_card():
    """The card is about finishing the BASE list. ProfileGame.progress is the whole-game percentage, so
    gating on it would deny a card to anyone whose game later shipped DLC."""
    profile = ProfileFactory()
    game, group, _ = _completed_game(profile, with_platinum=True, full_game_progress=61)
    dlc = TrophyGroupFactory(game=game, trophy_group_id='001')
    ProfileTrophyGroupFactory(profile=profile, trophy_group=dlc, progress=20)

    assert [c.trophy_group_id for c in cards.eligible_completions(profile)] == [group.id]


def test_dlc_groups_never_produce_their_own_card():
    profile = ProfileFactory()
    game, group, _ = _completed_game(profile, with_platinum=False)
    dlc = TrophyGroupFactory(game=game, trophy_group_id='001')
    ProfileTrophyGroupFactory(profile=profile, trophy_group=dlc, progress=100)

    assert [c.trophy_group_id for c in cards.eligible_completions(profile)] == [group.id]


def test_a_stale_standing_row_cannot_hide_a_completion():
    """Mirrors the badge engine's invariant: whole-game 100% implies the base list is done. A hunter
    looking at a 100% game on their profile must not find the card missing because a denorm lagged."""
    profile = ProfileFactory()
    _, group, _ = _completed_game(profile, with_platinum=True, progress=0, full_game_progress=100)

    assert [c.trophy_group_id for c in cards.eligible_completions(profile)] == [group.id]


def test_another_hunters_completion_is_not_yours():
    me, them = ProfileFactory(), ProfileFactory()
    _, group, _ = _completed_game(them, with_platinum=True)

    assert list(cards.eligible_completions(me)) == []
    assert cards.get_completion(me, group.id) is None


# ── Variants ──────────────────────────────────────────────────────────────────────────────────────

def test_variant_follows_whether_the_game_defines_a_platinum():
    profile = ProfileFactory()
    platted, _, _ = _completed_game(profile, with_platinum=True)
    completed, _, _ = _completed_game(profile, with_platinum=False)

    assert cards.resolve_variant(platted) == cards.PLATINUM
    assert cards.resolve_variant(completed) == cards.FULL


def test_variant_filter_splits_the_list_in_the_db():
    profile = ProfileFactory()
    _, plat_group, _ = _completed_game(profile, with_platinum=True)
    _, full_group, _ = _completed_game(profile, with_platinum=False)
    qs = cards.eligible_completions(profile)

    assert [c.trophy_group_id for c in cards.variant_filter(qs, cards.PLATINUM)] == [plat_group.id]
    assert [c.trophy_group_id for c in cards.variant_filter(qs, cards.FULL)] == [full_group.id]


def test_a_platinum_game_with_no_earned_row_falls_back_to_the_full_card():
    """A data anomaly, not a variant -- better a complete 100% card than a platinum card with an empty
    hero where the trophy should be."""
    profile = ProfileFactory()
    game = GameFactory(defined_trophies={'bronze': 5, 'platinum': 1})
    group = TrophyGroupFactory(game=game, trophy_group_id='default', defined_trophies={'bronze': 5, 'platinum': 1})
    standing = ProfileTrophyGroupFactory(profile=profile, trophy_group=group, progress=100)

    assert cards.get_card_data(profile, standing)['variant'] == cards.FULL


# ── Ordinals ──────────────────────────────────────────────────────────────────────────────────────

def test_each_variant_counts_its_own_ladder():
    """Independent counts on purpose: one shared ladder would have renumbered every platinum card
    already shared."""
    profile = ProfileFactory()
    for _ in range(3):
        _completed_game(profile, with_platinum=True)
    _, _, newest_full = _completed_game(profile, with_platinum=False)
    _, _, second_full = _completed_game(profile, with_platinum=False)
    second_full.last_trophy_at = timezone.now() + timezone.timedelta(hours=1)
    second_full.save(update_fields=['last_trophy_at'])

    assert cards.get_card_data(profile, newest_full)['ordinal'] == 1
    assert cards.get_card_data(profile, second_full)['ordinal'] == 2
    assert cards.get_card_data(profile, second_full)['ordinal_label'] == '100%'


def test_platinum_ordinal_counts_platinums_earned_up_to_this_one():
    from trophies.models import EarnedTrophy

    profile = ProfileFactory()
    _, _, first = _completed_game(profile, with_platinum=True)
    # Backdate the first platinum so the two have a defined order.
    EarnedTrophy.objects.filter(profile=profile).update(
        earned_date_time=timezone.now() - timezone.timedelta(days=30),
    )
    _, _, second = _completed_game(profile, with_platinum=True)

    assert cards.get_card_data(profile, first)['ordinal'] == 1
    assert cards.get_card_data(profile, second)['ordinal'] == 2
    assert cards.get_card_data(profile, second)['ordinal_label'] == 'Platinum'


# ── Badge lines: the NEW grouping-badge system ────────────────────────────────────────────────────

def test_badge_line_reads_the_new_system_and_names_a_held_title():
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    series = BadgeSeriesFactory(name='Norse Saga', title=Title.objects.create(name='Ragnarok Bearer'))
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    StageFactory(series_slug=series.series_slug).concepts.add(game.concept)
    UserTitle.objects.create(profile=profile, title=series.title, source_type='badge_series',
                             source_id=series.id)

    line = cards.get_card_data(profile, standing)['badge_lines'][0]

    assert line['series_name'] == 'Norse Saga'
    assert line['title'] == 'Ragnarok Bearer' and line['title_held'] is True


def test_a_series_with_no_live_edition_is_not_named():
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    series = BadgeSeriesFactory(name='Dormant')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=False)
    StageFactory(series_slug=series.series_slug).concepts.add(game.concept)

    assert cards.get_card_data(profile, standing)['badge_lines'] == []


def test_the_card_shows_the_title_the_hunter_is_wearing():
    """The identity strip is the hunter, so it carries their worn title rather than one this game
    happened to grant."""
    profile = ProfileFactory()
    _, _, standing = _completed_game(profile, with_platinum=True)
    UserTitle.objects.create(profile=profile, title=Title.objects.create(name='Case Hardened'),
                             source_type='badge_series', source_id=1, is_displayed=True)

    assert cards.get_card_data(profile, standing)['display_title'] == 'Case Hardened'


# ── Endpoints ─────────────────────────────────────────────────────────────────────────────────────

def test_html_endpoint_renders_the_card(client):
    profile = ProfileFactory()
    _, group, _ = _completed_game(profile, with_platinum=True, name='God of War Ragnarok')
    client.force_login(profile.user)

    resp = client.get(f'/api/v1/shareables/completion/{group.id}/html/')

    assert resp.status_code == 200
    body = resp.json()
    assert body['variant'] == 'platinum'
    assert 'God of War Ragnarok' in body['html']
    assert 'share-image-content' in body['html']
    # No unrendered template syntax leaked (multi-line {# #} is a known trap).
    assert '{%' not in body['html'] and '{#' not in body['html']


def test_the_full_variant_renders_its_own_label(client):
    profile = ProfileFactory()
    _, group, _ = _completed_game(profile, with_platinum=False)
    client.force_login(profile.user)

    body = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()

    assert body['variant'] == 'full'
    assert '100% Complete' in body['html']
    # The 100% mark stands in for the platinum trophy image, which this game has no row for.
    assert '>100</div>' in body['html']


def test_endpoint_refuses_someone_elses_completion(client):
    me, them = ProfileFactory(), ProfileFactory()
    _, group, _ = _completed_game(them, with_platinum=True)
    client.force_login(me.user)

    assert client.get(f'/api/v1/shareables/completion/{group.id}/html/').status_code == 404


def test_legacy_earned_trophy_url_resolves_to_the_same_card(client):
    """Platinum notifications already sent deep-link by EarnedTrophy id, and the old endpoints carry
    TokenAuthentication -- assume external consumers."""
    from trophies.models import EarnedTrophy

    profile = ProfileFactory()
    game, group, _ = _completed_game(profile, with_platinum=True, name='Bloodborne')
    et = EarnedTrophy.objects.get(profile=profile, trophy__game=game, trophy__trophy_type='platinum')
    client.force_login(profile.user)

    legacy = client.get(f'/api/v1/shareables/platinum/{et.id}/html/').json()
    native = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()

    assert legacy['trophy_group_id'] == native['trophy_group_id'] == group.id
    assert 'Bloodborne' in legacy['html']


def test_query_count_is_flat_as_the_completion_list_grows(django_assert_max_num_queries, client):
    profile = ProfileFactory()
    for _ in range(6):
        _completed_game(profile, with_platinum=True)
    _, group, _ = _completed_game(profile, with_platinum=True)
    client.force_login(profile.user)
    client.get(f'/api/v1/shareables/completion/{group.id}/html/')   # warm session/auth

    with django_assert_max_num_queries(16):
        client.get(f'/api/v1/shareables/completion/{group.id}/html/')


# ── Branding + density ────────────────────────────────────────────────────────────────────────────

def test_the_card_carries_real_branding(client):
    """These travel to people with no account, so the card is also the advert. The mark, the wordmark
    and the DOMAIN all have to be on it -- a muted line of text in a corner is not branding."""
    profile = ProfileFactory()
    _, group, _ = _completed_game(profile, with_platinum=True)
    client.force_login(profile.user)

    html = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['html']

    assert 'Platinum Pursuit' in html
    assert 'platpursuit.com' in html          # how a stranger finds us
    assert 'images/logo.png' in html          # the mark itself


def test_the_trophy_breakdown_is_on_the_card():
    """The tier split is the hobby's own vocabulary and reads fast even at embed size."""
    profile = ProfileFactory()
    _, group, standing = _completed_game(profile, with_platinum=True)
    standing.earned_trophies = {'platinum': 1, 'gold': 2, 'silver': 4, 'bronze': 9}
    standing.save(update_fields=['earned_trophies'])

    tiers = cards.get_card_data(profile, standing)['tier_counts']

    # Platinum first -- the order a hunter reads them in.
    assert [t['tier'] for t in tiers] == ['platinum', 'gold', 'silver', 'bronze']
    assert [t['count'] for t in tiers] == [1, 2, 4, 9]


def test_the_breakdown_falls_back_to_the_groups_defined_counts():
    """`earned_trophies` is a denorm that can lag. The group is finished either way, so the defined
    counts are the same numbers -- better that than an empty row."""
    profile = ProfileFactory()
    _, _, standing = _completed_game(profile, with_platinum=False)
    standing.earned_trophies = {}
    standing.save(update_fields=['earned_trophies'])

    tiers = cards.get_card_data(profile, standing)['tier_counts']

    assert [t['count'] for t in tiers] == [2, 4, 10]     # gold, silver, bronze (no platinum defined)


def test_the_identity_line_carries_career_totals():
    """Who is this person, for a stranger seeing the card cold."""
    profile = ProfileFactory()
    for _ in range(3):
        _completed_game(profile, with_platinum=True)
    _, _, standing = _completed_game(profile, with_platinum=False)

    data = cards.get_card_data(profile, standing)

    assert (data['total_platinums'], data['total_completions']) == (3, 1)


def test_the_hunters_own_rating_reaches_the_card(client):
    """Data nobody else on the card has -- and the reason the download flow nudges for a rating."""
    from trophies.models import UserConceptRating

    profile = ProfileFactory()
    game, group, standing = _completed_game(profile, with_platinum=True)
    UserConceptRating.objects.create(profile=profile, concept=game.concept, difficulty=6,
                                 fun_ranking=9, grindiness=5, hours_to_platinum=40, overall_rating=8)
    client.force_login(profile.user)

    html = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['html']

    assert cards.get_card_data(profile, standing)['user_rating']['difficulty'] == 6
    assert 'Difficulty' in html and 'Fun' in html


# ── Contract: the career half of the spine ────────────────────────────────────────────────────────

def _contracted_game(profile, **kw):
    """A completion whose concept is anchored + trusted-matched onto a live Contract."""
    from trophies.models import Contract, IGDBMatch, Job
    from django.utils import timezone as tz

    game, group, standing = _completed_game(profile, **kw)
    concept = game.concept
    concept.anchor_migration_completed_at = tz.now()
    concept.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatch.objects.create(concept=concept, igdb_id=4242, status=IGDBMatch.TRUSTED_STATUSES[0])
    contract = Contract.objects.create(name='Ragnarok', slug='ragnarok', igdb_id=4242, is_live=True)
    contract.jobs.add(
        Job.objects.create(slug='berserker', name='Berserker', discipline='combat'),
        Job.objects.create(slug='wayfarer', name='Wayfarer', discipline='exploration'),
    )
    return game, group, standing, contract


def test_the_contract_and_its_jobs_reach_the_card():
    """Badges are the collection, contracts are the career -- a card showing one and not the other
    tells half the story of what the completion was worth."""
    profile = ProfileFactory()
    _, _, standing, _ = _contracted_game(profile, with_platinum=True)

    contract = cards.get_card_data(profile, standing)['contract']

    assert contract['name'] == 'Ragnarok'
    assert sorted(contract['jobs']) == ['Berserker', 'Wayfarer']


def test_banked_contract_xp_is_read_from_the_ledger():
    """Amounts are paid at the multiplier active at grant time, so the card reports what was actually
    banked rather than recomputing it from current config."""
    from trophies.models import ContractXPGrant, EarnedContract

    profile = ProfileFactory()
    _, _, standing, contract = _contracted_game(profile, with_platinum=True)
    earned = EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True)
    # This contract's jobs only -- the 24-job catalogue is seeded, so Job.objects.all() is not it.
    for job in contract.jobs.all():
        ContractXPGrant.objects.create(profile=profile, job=job, amount=600,
                                       earned_contract=earned, tier='platinum')

    assert cards.get_card_data(profile, standing)['contract']['xp'] == 1200


def test_a_reached_but_unaccepted_contract_reads_as_unclaimed():
    """XP is sitting there waiting -- claiming is the action we want them to go take."""
    from trophies.models import EarnedContract

    profile = ProfileFactory()
    _, _, standing, contract = _contracted_game(profile, with_platinum=True)
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True)

    data = cards.get_card_data(profile, standing)['contract']
    assert data['xp'] == 0 and data['claimable'] is True


def test_a_game_with_no_contract_simply_omits_the_line():
    profile = ProfileFactory()
    _, _, standing = _completed_game(profile, with_platinum=True)

    assert cards.get_card_data(profile, standing)['contract'] is None
