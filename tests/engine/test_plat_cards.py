"""Plat card data layer + endpoints.

A card is earned by finishing a game's DEFAULT trophy group -- platinum or not. That rule is the whole
point of the 2026-08 rebuild: the old anchor was a platinum EarnedTrophy, which silently excluded every
100%-with-no-platinum game.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
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
def test_query_count_does_not_grow_with_the_completion_list(django_assert_num_queries, client):
    """Two sizes, same budget -- a single size against a fixed number says nothing about growth.

    The fixture carries a badge series, a contract and a rating on purpose: those are the paths that
    could fan out per row, and a budget that never exercises them is a budget that guards nothing.
    """
    from trophies.models import UserConceptRating

    def _measure(extra_completions):
        profile = ProfileFactory()
        for _ in range(extra_completions):
            _completed_game(profile, with_platinum=True)
        game, group, _ = _completed_game(profile, with_platinum=True)
        series = BadgeSeriesFactory(name='Norse Saga', title=Title.objects.create(name=f'T{extra_completions}'))
        GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
        StageFactory(series_slug=series.series_slug).concepts.add(game.concept)
        UserConceptRating.objects.create(profile=profile, concept=game.concept, difficulty=6,
                                         fun_ranking=9, grindiness=5, hours_to_platinum=40,
                                         overall_rating=4.0)
        client.force_login(profile.user)
        url = f'/api/v1/shareables/completion/{group.id}/html/'
        client.get(url)                      # warm session/auth
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(url).status_code == 200
        return len(ctx)

    small, large = _measure(1), _measure(12)

    assert small == large, f'query count grew with the list: {small} -> {large}'
    assert large <= 20, f'{large} queries for one card'



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


def test_the_identity_line_carries_the_platinum_count():
    """Who is this person, for a stranger seeing the card cold."""
    profile = ProfileFactory()
    for _ in range(3):
        _completed_game(profile, with_platinum=True)
    _, _, standing = _completed_game(profile, with_platinum=False)

    assert cards.get_card_data(profile, standing)['total_platinums'] == 3


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
    # Assert the VALUES -- 'Difficulty'/'Fun' are also plain words elsewhere in the document.
    assert '>6<' in html and '>9<' in html


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
        Job.objects.create(slug='berserker', name='Berserker', discipline='combat', icon='swords'),
        Job.objects.create(slug='wayfarer', name='Wayfarer', discipline='exploration', icon='compass'),
    )
    return game, group, standing, contract


def test_the_contract_and_its_jobs_reach_the_card():
    """Badges are the collection, contracts are the career -- a card showing one and not the other
    tells half the story of what the completion was worth."""
    profile = ProfileFactory()
    _, _, standing, _ = _contracted_game(profile, with_platinum=True)

    contract = cards.get_card_data(profile, standing)['contract']

    assert contract['name'] == 'Ragnarok'
    assert sorted(j['name'] for j in contract['jobs']) == ['Berserker', 'Wayfarer']
    # Discipline colour + real Lucide geometry, so the card draws the same glyph the site does.
    by_name = {j['name']: j for j in contract['jobs']}
    assert by_name['Berserker']['colour'] == '#fc5855'          # combat
    assert by_name['Wayfarer']['colour'] == '#59d38c'           # exploration
    assert by_name['Berserker']['icon_paths'].startswith('<')


def test_a_game_with_no_contract_simply_omits_the_line():
    profile = ProfileFactory()
    _, _, standing = _completed_game(profile, with_platinum=True)

    assert cards.get_card_data(profile, standing)['contract'] is None


def test_the_star_score_reaches_the_card_with_a_fill_percentage():
    """`overall_rating` is a 0.5-5.0 FLOAT, unlike the 1-10 difficulty/fun ints, so the card draws a
    clipped overlay and half stars have to be exact."""
    from trophies.models import UserConceptRating

    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    UserConceptRating.objects.create(profile=profile, concept=game.concept, difficulty=6,
                                     fun_ranking=9, grindiness=5, hours_to_platinum=40,
                                     overall_rating=4.5)

    rating = cards.get_card_data(profile, standing)['user_rating']

    assert rating['overall_rating'] == 4.5
    assert rating['stars_pct'] == 90.0


def test_the_badge_line_carries_real_medallion_art_and_its_edition():
    """The medallion is the product's signature object, so the card shows the badge rather than just
    naming it -- and the EDITION, because Ultra HD and Legacy HD are different badges to a hunter."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    series = BadgeSeriesFactory(name='Norse Saga')
    GroupBadgeFactory(
        series=series,
        platform_group=PlatformGroupFactory(name='Ultra HD', key='ultra-hd', platforms=['PS4', 'PS5']),
        is_live=True,
    )
    StageFactory(series_slug=series.series_slug).concepts.add(game.concept)

    line = cards.get_card_data(profile, standing)['badge_lines'][0]

    assert line['edition'] == 'Ultra HD'
    assert line['medallion_layers'], 'medallion should always resolve at least the default art'
    # The edition label, the ring and the progress bar all wear the badge's backing metal -- the same
    # source-of-truth hex as .pp-med[data-tier] in badge-medallion.css.
    assert line['medallion_colour'] == '#8fd2ea'


def test_a_legacy_hd_edition_wears_the_gold_metal():
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    series = BadgeSeriesFactory(name='Norse Saga')
    GroupBadgeFactory(series=series, is_live=True, platform_group=PlatformGroupFactory(
        name='Legacy HD', key='legacy-hd', platforms=['PS3', 'PS4', 'PS5']))
    StageFactory(series_slug=series.series_slug).concepts.add(game.concept)

    line = cards.get_card_data(profile, standing)['badge_lines'][0]

    assert line['medallion_colour'] == '#e7c25c'      # legacy-hd backs gold


def test_the_medallion_picks_the_edition_that_covers_this_game():
    """A PS5 completion must not show the Legacy HD medallion."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)   # GameFactory defaults to PS5
    series = BadgeSeriesFactory(name='Norse Saga')
    GroupBadgeFactory(series=series, is_live=True, platform_group=PlatformGroupFactory(
        name='Legacy HD', key='legacy-hd', platforms=['PS3', 'PSVITA']))
    GroupBadgeFactory(series=series, is_live=True, platform_group=PlatformGroupFactory(
        name='Ultra HD', key='ultra-hd', platforms=['PS4', 'PS5']))
    StageFactory(series_slug=series.series_slug).concepts.add(game.concept)

    assert cards.get_card_data(profile, standing)['badge_lines'][0]['edition'] == 'Ultra HD'


def test_only_one_badge_is_resolved():
    """The spine band draws `badge_lines.0` and nothing else, so resolving a second line meant a sort,
    a standings lookup and a title lookup thrown away on every render."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    for name in ('Alpha Series', 'Beta Series', 'Gamma Series'):
        series = BadgeSeriesFactory(name=name)
        GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
        StageFactory(series_slug=series.series_slug).concepts.add(game.concept)

    lines = cards.get_card_data(profile, standing)['badge_lines']

    assert len(lines) == 1
    assert lines[0]['medallion_layers'], 'the one line the card draws must carry its art'


def test_every_contract_job_is_named_on_the_card():
    """All 6 fit at the maximum. The card shows no XP figure on purpose: a contract's XP splits evenly
    across its jobs, so a single total only reads correctly with every job beside it -- and naming them
    is the more useful half of that pair."""
    from trophies.models import Job

    profile = ProfileFactory()
    _, _, standing, contract = _contracted_game(profile, with_platinum=True)
    for i, disc in enumerate(['mind', 'heart', 'finesse', 'combat']):
        contract.jobs.add(Job.objects.create(slug=f'j{i}', name=f'Job {i}', discipline=disc, icon='sparkles'))

    data = cards.get_card_data(profile, standing)['contract']

    assert len(data['jobs']) == 6                       # 2 from the fixture + 4 here, none dropped
    assert all(j['name'] and j['colour'] for j in data['jobs'])
    assert 'xp' not in data and 'claimable' not in data


# ── Which badge leads when a game is in several ───────────────────────────────────────────────────

def _attributed_series(concept, name, *, attribution=None, badge_type='series'):
    """A live badge series this concept belongs to, carrying at most one attribution FK.

    `badge_type` defaults to 'series' throughout these tests ON PURPOSE: the lead-badge rule reads the
    attribution FKs, not the type label, and defaulting the label the "wrong" way is what proves it.
    """
    from trophies.models import Company, Franchise

    kwargs = {}
    if attribution == 'collection':
        kwargs['collection'] = Franchise.objects.create(
            igdb_id=9001, name=f'{name} Collection', slug=f'{name.lower().replace(" ", "-")}-c',
            source_type='collection')
    elif attribution == 'franchise':
        kwargs['franchise'] = Franchise.objects.create(
            igdb_id=9002, name=f'{name} Franchise', slug=f'{name.lower().replace(" ", "-")}-f',
            source_type='franchise')
    elif attribution == 'developer':
        kwargs['developer'] = Company.objects.create(
            igdb_id=9003, name=f'{name} Studio', slug=f'{name.lower().replace(" ", "-")}-d')

    series = BadgeSeriesFactory(name=name, badge_type=badge_type, **kwargs)
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    StageFactory(series_slug=series.series_slug).concepts.add(concept)
    return series


def test_the_lead_badge_follows_the_attribution_the_series_carries():
    """A game sits in a collection badge, a franchise badge, its studio's badge and a bare series at
    once. The card shows ONE, and it must be the same one a browse card leads with -- otherwise the
    two surfaces disagree about what the game is.

    Every series here is badge_type='series'. That is the point: the rule reads the attribution FKs, so
    ordering must hold even when the type label says otherwise."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    for name, attribution in [('Studio Badge', 'developer'), ('Own Series', None),
                              ('The Franchise', 'franchise'), ('The Collection', 'collection')]:
        _attributed_series(game.concept, name, attribution=attribution)

    lines = cards.get_card_data(profile, standing)['badge_lines']

    # collection > franchise > developer > no attribution at all.
    assert lines[0]['series_name'] == 'The Collection'


def test_a_type_label_does_not_override_the_attribution():
    """The regression this rule was corrected for.

    A 'collection'-TYPE badge carrying no collection FK must not outrank a plain series that actually
    has one. Type is a flavour label; the attribution is the fact."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    _attributed_series(game.concept, 'Labelled Only', attribution=None, badge_type='collection')
    _attributed_series(game.concept, 'Really Attributed', attribution='collection')

    assert cards.get_card_data(profile, standing)['badge_lines'][0]['series_name'] == 'Really Attributed'


def test_attribution_outranks_a_held_title():
    """Consequence of ordering by attribution first, stated so it can't surprise anyone later: holding
    an unattributed series' title does not promote it over an unheld collection badge. The attribution
    is the stronger signal of what a game IS, and the card is showing the game."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    _attributed_series(game.concept, 'The Collection', attribution='collection')
    owned = _attributed_series(game.concept, 'Own Series', attribution=None)
    owned.title = Title.objects.create(name='Held Title')
    owned.save(update_fields=['title'])
    UserTitle.objects.create(profile=profile, title=owned.title, source_type='badge_series',
                             source_id=owned.id)

    assert cards.get_card_data(profile, standing)['badge_lines'][0]['series_name'] == 'The Collection'


def test_a_held_title_still_wins_within_one_attribution_rank():
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    _attributed_series(game.concept, 'Alpha Series', attribution=None)
    owned = _attributed_series(game.concept, 'Zeta Series', attribution=None)
    owned.title = Title.objects.create(name='Held Title')
    owned.save(update_fields=['title'])
    UserTitle.objects.create(profile=profile, title=owned.title, source_type='badge_series',
                             source_id=owned.id)

    assert cards.get_card_data(profile, standing)['badge_lines'][0]['series_name'] == 'Zeta Series'


# ── DLC: celebrate a full clear, never penalise an unfinished one ─────────────────────────────────

def _with_dlc(profile, game, *, cleared):
    """Give the game a DLC group and set the hunter's whole-game progress accordingly."""
    from trophies.models import ProfileGame

    dlc = {'bronze': 20, 'silver': 10, 'gold': 5}
    game.defined_trophies = {'bronze': 30, 'silver': 14, 'gold': 7, 'platinum': 1}   # base + dlc
    game.save(update_fields=['defined_trophies'])
    group = TrophyGroupFactory(game=game, trophy_group_id='001', defined_trophies=dlc)
    ProfileTrophyGroupFactory(profile=profile, trophy_group=group, progress=100 if cleared else 30)
    ProfileGame.objects.filter(profile=profile, game=game).update(progress=100 if cleared else 71)


def test_clearing_every_dlc_is_celebrated_on_the_card():
    """A hunter who cleared EVERYTHING looked identical to one who cleared only the base list. The
    whole-game figures appear once the whole game is done."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    _with_dlc(profile, game, cleared=True)

    data = cards.get_card_data(profile, standing)

    assert data['all_dlc_done'] is True
    assert data['trophy_total'] == 52                       # 30+14+7+1, the whole game
    # The breakdown has to describe the same scope as the total, or the dots contradict the label.
    assert sum(t['count'] for t in data['tier_counts']) == 52


def test_outstanding_dlc_is_never_shown_as_a_shortfall():
    """"51 of 71" beside a PLATINUM label reads as unfinished. The card stays scoped to the base list
    and says nothing about the DLC."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    _with_dlc(profile, game, cleared=False)

    data = cards.get_card_data(profile, standing)

    assert data['all_dlc_done'] is False
    assert data['trophy_total'] == 17                       # the default group only: 10+4+2+1
    assert sum(t['count'] for t in data['tier_counts']) == 17


def test_a_game_with_no_dlc_makes_no_dlc_claim():
    """"ALL DLC" on a game that never had any would be nonsense."""
    profile = ProfileFactory()
    _, _, standing = _completed_game(profile, with_platinum=True, full_game_progress=100)

    assert cards.get_card_data(profile, standing)['all_dlc_done'] is False


def test_the_medallion_actually_reaches_the_rendered_card(client):
    """Asserting the SERVICE output is why this shipped broken. group_medallion_layers returns
    `static(...)` paths for the backdrop fallback and the default subject art, and ShareImageCache
    hard-rejects any non-http scheme -- so every static layer was silently dropped and a badge with no
    custom image rendered with no medallion at all. Assert the <img> in the HTML, not the dict."""
    profile = ProfileFactory()
    game, group, _ = _completed_game(profile, with_platinum=True)
    series = BadgeSeriesFactory(name='Norse Saga')            # no badge_image -> static default art
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    StageFactory(series_slug=series.series_slug).concepts.add(game.concept)
    client.force_login(profile.user)

    html = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['html']

    assert 'images/badges/' in html, 'the medallion art never made it into the card'
    assert 'Norse Saga' in html


def test_the_card_draws_the_badge_subject_without_its_backdrop_plate(client):
    """The card shows the badge's own silhouette, so it carries no plate and no circle mask.

    `group_medallion_layers` returns [backdrop_plate, subject]. The plate exists to sit behind a circle
    mask on the badge pages; unmasked it renders as its own shape behind a shield and reads as a stray
    sliver of metal. The badge PAGES keep both layers -- this trim is the card's alone."""
    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    series = BadgeSeriesFactory(name='Norse Saga')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    StageFactory(series_slug=series.series_slug).concepts.add(game.concept)

    line = cards.get_card_data(profile, standing)['badge_lines'][0]

    assert len(line['medallion_layers']) == 1, 'the plate should have been dropped'
    assert 'backdrop' not in line['medallion_layers'][0]


def test_the_medallion_is_contained_and_unframed(client):
    """Cropping was the bug: `border-radius: 50%` + `object-fit: cover` cut the points off every
    shield, so distinct badges all resolved into the same disc at card size."""
    profile = ProfileFactory()
    game, group, _ = _completed_game(profile, with_platinum=True)
    series = BadgeSeriesFactory(name='Norse Saga')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    StageFactory(series_slug=series.series_slug).concepts.add(game.concept)
    client.force_login(profile.user)

    html = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['html']

    assert 'object-fit: contain' in html, 'the art must not be cropped to fill'
    # The edition label still wears the backing metal, so the colour itself is expected in the HTML --
    # what must be gone is a border drawn around the art.
    assert 'border-radius: 50%; overflow: hidden; border:' not in html


# ── Art grounds are offered only when the game HAS art ────────────────────────────────────────────

def test_a_game_with_no_art_offers_no_art_ground(client):
    """The picker must not offer a ground that silently falls back to a gradient -- that was the
    preview-lies-about-the-download class of bug all over again."""
    profile = ProfileFactory()
    _, group, standing = _completed_game(profile, with_platinum=True)
    client.force_login(profile.user)

    assert cards.get_card_data(profile, standing)['art_urls'] == []
    assert client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['art_options'] == []


def test_every_landscape_image_is_offered_as_its_own_ground():
    """`landscape_urls` is ordered by quality (trusted IGDB screenshots -> artworks -> PSN bg), so a
    game with several gives a real choice of backdrop rather than one take-it-or-leave-it."""
    from trophies.models import IGDBMatch

    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    IGDBMatch.objects.create(
        concept=game.concept, igdb_id=99, status=IGDBMatch.TRUSTED_STATUSES[0],
        igdb_screenshot_image_ids=['aaa', 'bbb', 'ccc'],
    )

    urls = cards.get_card_data(profile, standing)['art_urls']

    assert len(urls) == 3 and all(u.startswith('https://images.igdb.com/') for u in urls)


def test_the_art_list_is_capped():
    """Each option is another image to cache and another swatch; past a handful it stops being a
    choice."""
    from trophies.models import IGDBMatch

    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    IGDBMatch.objects.create(
        concept=game.concept, igdb_id=99, status=IGDBMatch.TRUSTED_STATUSES[0],
        igdb_screenshot_image_ids=[f'shot{i}' for i in range(12)],
    )

    assert len(cards.get_card_data(profile, standing)['art_urls']) == cards.ART_OPTION_CAP


def test_the_cover_blur_ground_is_gone():
    """A 3:4 cover blown up to 1200x630 is mostly upscale, and it looked it."""
    from trophies.themes import PLAT_CARD_THEME_KEYS

    assert 'ppArtCover' not in PLAT_CARD_THEME_KEYS
    assert 'gameArtBlur' not in PLAT_CARD_THEME_KEYS


def test_every_curated_key_resolves_to_a_real_theme():
    """`get_plat_card_themes` SKIPS a key it can't find rather than raising, so a typo doesn't break the
    page -- the ground just silently never appears in the picker. That is exactly the failure nobody
    notices, so the list and the registry are pinned to each other here."""
    from trophies.themes import PLAT_CARD_THEME_KEYS, get_plat_card_themes

    resolved = get_plat_card_themes()

    assert [k for k, _ in resolved] == PLAT_CARD_THEME_KEYS, 'a key failed to resolve'
    for key, entry in resolved:
        assert entry['name'] and entry['background_css'], f'{key} resolved without a ground'


def test_the_picker_still_fits_on_one_row():
    """The swatch grid is `repeat(auto-fit, minmax(70px, 1fr))` in a 1000px box (plat-cards.css), which
    yields 12 columns: floor((1000 - 36 padding + 7 gap) / (70 + 7)) == 12.

    Every ground has to stay on screen -- no "more" control, no scrolling to reach one -- and the modal
    must not scroll, so a second row costs the preview ~120px of height. 8 grounds + ART_OPTION_CAP is
    exactly 12 today; a ninth ground wraps it. Adding one is fine, but the CSS min-width has to come
    down in the same change, which is what this test is here to say."""
    from core.services.completion_card_service import ART_OPTION_CAP
    from trophies.themes import PLAT_CARD_THEME_KEYS

    fixed = [k for k in PLAT_CARD_THEME_KEYS if k != 'ppArt']

    assert len(fixed) + ART_OPTION_CAP <= 12, (
        f'{len(fixed)} grounds + {ART_OPTION_CAP} art swatches needs a second row; '
        'lower the minmax() floor in .pc-modal__themes to match'
    )


def test_card_grounds_never_leak_into_the_site_theme_pickers():
    """The card's grounds are drawn for ONE artifact at 1200x630. They live in the shared registry so the
    card can reuse the rendering pipeline, but every exporter feeding a site-wide picker must filter them
    out. Only `get_available_themes_for_grid` did: `get_themes_for_js` ships as window.GRADIENT_THEMES and
    the Monthly Recap builds its background dropdown by iterating it, so every card ground was offered as
    a recap background -- four of them, then seven once the lighter grounds landed."""
    from trophies.themes import (
        GRADIENT_THEMES, PLAT_CARD_CATEGORY, THEME_CHOICES, get_available_themes_for_grid,
        get_themes_for_js,
    )

    def card_grounds_in(keys):
        return [k for k in keys if GRADIENT_THEMES.get(k, {}).get('category') == PLAT_CARD_CATEGORY]

    assert not card_grounds_in(get_themes_for_js())
    assert not card_grounds_in(k for k, _label in THEME_CHOICES if k)
    assert not card_grounds_in(key for key, _data in get_available_themes_for_grid())


def test_retro_wave_stays_a_site_theme_too():
    """The counterpart risk: pulling retroWave into the card's curation is exactly the change that tempts
    someone to recategorise it to `plat_card` for tidiness -- which the filter above would then delete
    from every site picker it has always appeared in."""
    from trophies.themes import THEME_CHOICES, get_themes_for_js

    assert 'retroWave' in get_themes_for_js()
    assert 'retroWave' in dict(THEME_CHOICES)


def test_the_modal_height_budget_matches_the_css_cap():
    """`fit()` budgets the preview against `window.innerHeight * 0.92` and `.pc-modal__box` caps at 92vh.
    If the CSS drops below the JS figure, the box hits the smaller cap while JS still hands out the
    larger one -- and since the box is `overflow: hidden`, the swatch row is clipped with NO scrollbar.
    Silent, and only on short viewports. Two files, one number, nothing else connecting them."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    js = (root / 'static/js/plat-cards.js').read_text(encoding='utf-8')
    css = (root / 'static/css/components/plat-cards.css').read_text(encoding='utf-8')

    js_vh = float(re.search(r'var BOX_VH = ([\d.]+)', js).group(1))
    css_vh = float(re.search(r'\.pc-modal__box\s*\{[^}]*?max-height:\s*(\d+)vh', css, re.S).group(1)) / 100

    assert js_vh == css_vh, f'fit() budgets {js_vh:.0%} but .pc-modal__box caps at {css_vh:.0%}'


def test_the_curated_set_reuses_the_existing_retro_wave_theme():
    """Retro Wave is a SITE theme (category 'retro'), pulled into the card's curation unchanged rather
    than redrawn for it -- a card-local copy would drift from the one the site pickers show. It is also
    the one curated ground whose background is multi-layer, so it guards `_clean_css` handling that
    the single-gradient house grounds never exercise."""
    from trophies.themes import GRADIENT_THEMES, PLAT_CARD_THEME_KEYS, get_plat_card_themes

    assert 'retroWave' in PLAT_CARD_THEME_KEYS
    assert GRADIENT_THEMES['retroWave']['category'] == 'retro', 'reused in place, not recategorised'

    css = dict(get_plat_card_themes())['retroWave']['background_css']
    assert css.count('gradient(') >= 2 and '\n' not in css


# ── The hunter's verdict: three axes + their own words ────────────────────────────────────────────

def _rate(profile, concept, **kw):
    from trophies.models import UserConceptRating
    fields = dict(difficulty=9, grindiness=7, fun_ranking=9, hours_to_platinum=40, overall_rating=4.5)
    fields.update(kw)
    return UserConceptRating.objects.create(profile=profile, concept=concept, **fields)


def test_all_three_rating_axes_reach_the_card(client):
    """Grindiness was collected all along (the rate-before-download prompt asks for it) and never
    shown, which left the stats row stopping short with a visible void after it."""
    profile = ProfileFactory()
    game, group, standing = _completed_game(profile, with_platinum=True)
    _rate(profile, game.concept)
    client.force_login(profile.user)

    rating = cards.get_card_data(profile, standing)['user_rating']
    html = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['html']

    assert (rating['difficulty'], rating['grindiness'], rating['fun_ranking']) == (9, 7, 9)
    assert 'Difficulty' in html and 'Grind' in html and 'Fun' in html


def test_the_payload_carries_the_rating_so_an_edit_opens_prefilled(client):
    """The share modal can reopen the rate form to CHANGE a rating, and it has to open on the hunter's
    real scores. The payload used to carry only `has_rating`, so an edit would have opened on the form's
    defaults and saving would have silently overwritten their scores with 3/5/5/5 -- an edit control that
    destroys the thing it edits."""
    profile = ProfileFactory()
    game, group, _ = _completed_game(profile, with_platinum=True)
    _rate(profile, game.concept, difficulty=8, grindiness=6, fun_ranking=10, overall_rating=4.5,
          hours_to_platinum=42)
    client.force_login(profile.user)

    body = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()

    assert body['has_rating'] is True
    r = body['user_rating']
    # Every field the form posts back must be present, or that axis silently resets on save.
    assert (r['overall_rating'], r['difficulty'], r['grindiness'], r['fun_ranking'],
            r['hours_to_platinum']) == (4.5, 8, 6, 10, 42)


def test_an_unrated_card_reports_no_rating_to_prefill_from(client):
    """`user_rating` is None rather than a zeroed dict, so the form keeps its own defaults."""
    profile = ProfileFactory()
    _, group, _ = _completed_game(profile, with_platinum=True)
    client.force_login(profile.user)

    body = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()

    assert body['has_rating'] is False
    assert body['user_rating'] is None


def test_the_share_modal_offers_a_rating_control(client):
    """The card's stats ARE the hunter's rating, so it can be fixed without leaving. Ships hidden --
    there is nothing to rate until a card loads, and the previous card's label would otherwise offer to
    edit a rating belonging to a different game."""
    from django.template.loader import render_to_string
    from trophies.themes import get_plat_card_themes

    themes = get_plat_card_themes()
    html = render_to_string('shareables/partials/share_modal.html',
                            {'card_themes': themes, 'card_theme_js': {}})

    assert 'data-share-rate' in html and 'data-share-rate-label' in html
    assert 'hidden' in html.split('data-share-rate')[1].split('>')[0], 'must start hidden'


def test_the_rate_modal_exposes_the_hooks_the_share_flow_relabels(client):
    """The shared modal is written for the download prompt ("Rate and Download" / "Skip, just
    download"). Neither is true of an explicit edit, so plat-cards.js swaps both -- which needs the
    submit label in its own element rather than as a bare text node beside the icon."""
    from django.template.loader import render_to_string

    html = render_to_string('partials/rate_before_download_modal.html')

    assert 'data-rbd-submit-label' in html
    assert 'id="rbd-prompt-copy"' in html
    assert 'id="rbd-skip-btn"' in html


def test_the_rating_values_wear_the_sites_tone_colours(client):
    """Uses the same `rating_tone` filter the Ratings tab does, so the two surfaces can't drift.
    Note the polarity is consumer-advice polarity: a HARD game is `bad`/red here, not a flex."""
    profile = ProfileFactory()
    game, group, _ = _completed_game(profile, with_platinum=True)
    _rate(profile, game.concept, difficulty=9, fun_ranking=9)
    client.force_login(profile.user)

    html = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['html']

    assert '#ff5860' in html, 'difficulty 9 should carry the `bad` tone'
    assert '#3add9e' in html, 'fun 9 should carry the `good` tone'


def test_the_blurb_reaches_the_card(client):
    """The one thing on the card no other hunter's card can have."""
    profile = ProfileFactory()
    game, group, standing = _completed_game(profile, with_platinum=True)
    _rate(profile, game.concept, blurb='Brutal, but the combat never stopped being satisfying.')
    client.force_login(profile.user)

    html = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['html']

    assert 'Brutal, but the combat never stopped being satisfying.' in html


def test_a_staff_hidden_blurb_never_renders(client):
    """`blurb_hidden` is the staff soft-hide for an inappropriate quick take. The card is an image
    that leaves the site, so this is the one place a missed moderation flag can't be taken back."""
    profile = ProfileFactory()
    game, group, standing = _completed_game(profile, with_platinum=True)
    _rate(profile, game.concept, blurb='something unpleasant', blurb_hidden=True)
    client.force_login(profile.user)

    assert cards.get_card_data(profile, standing)['user_rating']['blurb'] == ''
    assert 'something unpleasant' not in client.get(
        f'/api/v1/shareables/completion/{group.id}/html/').json()['html']


def test_the_card_holds_up_with_no_blurb(client):
    """Most ratings carry none, so the blurb is the uncommon case -- the layout has to stand without
    it rather than being sized around it."""
    profile = ProfileFactory()
    game, group, _ = _completed_game(profile, with_platinum=True)
    _rate(profile, game.concept, blurb='')
    client.force_login(profile.user)

    html = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['html']

    assert '&ldquo;' not in html and 'Grind' in html
