"""A trophy group as a stage member (`Stage.trophy_groups`), for a game that ships inside another list.

The Kingdom Hearts PS5 collections carry whole games as DLC groups of one list, so finishing Re:Chain of
Memories there never shows up as a finished LIST. The owner's rules (2026-10-24):

  * the group at 100% clears its stage AND counts for holo: the group is the whole game it stands for
  * no cross-credit either way: the list's own platinum (its default group) never clears a group's stage,
    and a group never clears a stage that lists the whole list
  * every question of the form "which series is this game in?" follows the new path too
"""
from datetime import timedelta

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, ConceptTrophyGroupFactory, GameFactory, GroupBadgeFactory,
    PlatformGroupFactory, ProfileFactory, StageFactory, TrophyGroupFactory,
)
from trophies.models import (
    Concept, ConceptBundle, ConceptTrophyGroup, ProfileGame, ProfileTrophyGroup, Stage,
)
from trophies.services.badge_orchestrator import (
    build_catalog, evaluate_with_catalog, recompute_required_stages,
)
from trophies.services.stage_membership import in_series_q, stage_has_concept_q

pytestmark = pytest.mark.django_db


def _collection(*, groups=('001',), title='Kingdom Hearts Collection', platforms=('PS5',), **game_kwargs):
    """A list carrying whole games as trophy groups: its concept, one version, and a CTG + TrophyGroup per
    group id (plus its 'default'). Returns (concept, game, {group id: ConceptTrophyGroup})."""
    concept = ConceptFactory(unified_title=title)
    game = GameFactory(concept=concept, title_name=title, title_platform=list(platforms), **game_kwargs)
    ctgs = {}
    for gid in ('default',) + tuple(groups):
        TrophyGroupFactory(game=game, trophy_group_id=gid)
        ctgs[gid] = ConceptTrophyGroupFactory(concept=concept, trophy_group_id=gid, display_name=f'Game {gid}')
    return concept, game, ctgs


def _series(*stage_specs):
    """A series whose stages are given as callables taking the new Stage. Returns its Ultra HD GroupBadge."""
    series = BadgeSeriesFactory()
    for number, fill in enumerate(stage_specs, start=1):
        fill(StageFactory(series_slug=series.series_slug, stage_number=number))
    return GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='ultra-hd', platforms=['PS4', 'PS5']))


def _progress(profile, game, gid, pct, when=None):
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id=gid),
        defaults={'progress': pct, 'last_trophy_at': when or timezone.now()},
    )


def _list_progress(profile, game, pct):
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': pct})


def _evaluate(profile, gb, whole_catalogue=False):
    return evaluate_with_catalog(profile, build_catalog([gb], whole_catalogue=whole_catalogue))[gb.id]


# --- the bars ------------------------------------------------------------------------------------------

@pytest.mark.parametrize('whole_catalogue', [False, True])
def test_a_finished_group_clears_its_stage_and_counts_for_holo(whole_catalogue):
    """Both regimes, because the group's progress row reaches the read by a different filter in each."""
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    profile = ProfileFactory()
    _list_progress(profile, game, 30)          # the collection as a whole is nowhere near done
    _progress(profile, game, '001', 100)

    result = _evaluate(profile, gb, whole_catalogue)

    assert result.gating_count == 1
    assert result.base_satisfied_count == 1, 'the finished group did not clear its stage'
    assert result.holo_satisfied_count == 1, 'the group IS the whole game, so 100% of it is holo too'


@pytest.mark.parametrize('whole_catalogue', [False, True])
def test_the_lists_own_platinum_never_clears_a_groups_stage(whole_catalogue):
    """No cross-credit in the reverse direction: the collection's plat sits in its default group."""
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    profile = ProfileFactory()
    _list_progress(profile, game, 80)
    _progress(profile, game, 'default', 100)
    _progress(profile, game, '001', 40)

    assert _evaluate(profile, gb, whole_catalogue).base_satisfied_count == 0


def test_a_finished_group_never_clears_a_stage_that_lists_the_whole_list():
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']), lambda st: st.concepts.add(concept))
    profile = ProfileFactory()
    _list_progress(profile, game, 30)
    _progress(profile, game, 'default', 20)
    _progress(profile, game, '001', 100)

    stages = {s.stage_number: s for s in _evaluate(profile, gb).stages}

    assert stages[1].base_satisfied, 'the group stage'
    assert not stages[2].base_satisfied, 'the whole-list stage asks for the list, not one game inside it'


def test_a_finished_list_clears_its_group_stages_even_with_a_stale_group_row():
    """game_state's invariant, one level down: 100% of the list means 100% of every group in it."""
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    profile = ProfileFactory()
    _list_progress(profile, game, 100)
    _progress(profile, game, '001', 60)        # stale

    assert _evaluate(profile, gb).base_satisfied_count == 1


def test_a_version_without_the_group_is_not_a_way_to_clear_it():
    """A CTG is concept-level; a version of the list that lacks the group (say a region without the add-on)
    carries nothing that could finish it, even at 100%."""
    concept, with_group, ctgs = _collection()
    without = GameFactory(concept=concept, title_platform=['PS5'])
    TrophyGroupFactory(game=without, trophy_group_id='default')
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    profile = ProfileFactory()
    _list_progress(profile, without, 100)

    assert _evaluate(profile, gb).base_satisfied_count == 0


def test_the_stage_is_dated_by_the_groups_last_trophy():
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    profile = ProfileFactory()
    when = timezone.now() - timedelta(days=40)
    _list_progress(profile, game, 30)
    _progress(profile, game, '001', 100, when=when)

    result = _evaluate(profile, gb)

    assert result.stages[0].base_date == when
    assert result.earned_date == when


def test_a_group_stage_gates_like_any_stage_and_retires_with_its_list():
    """Gating reads the list's own status: obtainable gates, unobtainable retires."""
    _, _, live = _collection(title='Live Collection')
    _, _, dead = _collection(title='Dead Collection', is_obtainable=False)
    gb = _series(lambda st: st.trophy_groups.add(live['001']), lambda st: st.trophy_groups.add(dead['001']))

    recompute_required_stages(build_catalog([gb]))
    gb.refresh_from_db()

    assert gb.required_stages == 1


# --- membership: every "which series is this game in?" question ---------------------------------------

def test_the_membership_helpers_follow_the_trophy_group_path():
    concept, game, ctgs = _collection()
    stage = StageFactory(series_slug='kh')
    stage.trophy_groups.add(ctgs['001'])

    assert set(Concept.objects.filter(in_series_q(['kh']))) == {concept}
    assert game.__class__.objects.filter(in_series_q(['kh'], 'concept__')).get() == game
    assert Stage.objects.filter(stage_has_concept_q([concept.id])).get() == stage


@pytest.mark.parametrize('path', ['direct', 'bundle', 'group'])
def test_the_membership_helpers_follow_every_path(path):
    """Pins all three, so a helper that dropped one fails here rather than in a silent sync gap."""
    concept, _, ctgs = _collection()
    stage = StageFactory(series_slug='s')
    if path == 'direct':
        stage.concepts.add(concept)
    elif path == 'bundle':
        ConceptBundle.objects.create(stage=stage, label='Episodes').concepts.add(concept)
    else:
        stage.trophy_groups.add(ctgs['001'])

    assert Concept.objects.filter(in_series_q(['s'])).distinct().get() == concept
    assert Stage.objects.filter(stage_has_concept_q([concept.id])).distinct().get() == stage
    assert not Concept.objects.filter(in_series_q(['other'])).exists()


def test_syncing_the_list_re_evaluates_a_series_reached_only_through_a_group():
    from trophies.services.badge_apply import evaluate_for_touched_games

    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    profile = ProfileFactory()
    _list_progress(profile, game, 30)
    _progress(profile, game, '001', 100)
    pg = ProfileGame.objects.get(profile=profile, game=game)

    out = evaluate_for_touched_games(profile, [pg.id], notify=False)

    assert out['awarded'] == [gb.id]


def test_the_coverage_audit_counts_the_list_as_covered():
    from trophies.services.badge_coverage_service import covered_concept_ids

    concept, _, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))

    assert concept.id in covered_concept_ids(gb.series.series_slug)


# --- Concept.absorb --------------------------------------------------------------------------------------

def test_absorb_keeps_a_stage_member_on_a_doomed_duplicate_group():
    """The CTG-cascade trap: other's '001' duplicates the survivor's and is deleted with `other`, so its stage
    row must move to the survivor's '001' first. A unique group moves with its stage rows intact."""
    survivor = ConceptFactory()
    ConceptTrophyGroupFactory(concept=survivor, trophy_group_id='001', display_name='KH Re:CoM')
    other = ConceptFactory()
    dup = ConceptTrophyGroupFactory(concept=other, trophy_group_id='001', display_name='KH Re:CoM')
    unique = ConceptTrophyGroupFactory(concept=other, trophy_group_id='002', display_name='KH II')
    stage = StageFactory(series_slug='kh')
    stage.trophy_groups.add(dup, unique)

    survivor.absorb(other)
    other.delete()

    kept = ConceptTrophyGroup.objects.filter(stages=stage)
    assert {(g.concept_id, g.trophy_group_id) for g in kept} == {(survivor.id, '001'), (survivor.id, '002')}


def test_absorb_does_not_duplicate_a_group_the_stage_already_had():
    survivor = ConceptFactory()
    kept = ConceptTrophyGroupFactory(concept=survivor, trophy_group_id='001')
    other = ConceptFactory()
    dup = ConceptTrophyGroupFactory(concept=other, trophy_group_id='001')
    stage = StageFactory(series_slug='kh')
    stage.trophy_groups.add(kept, dup)

    survivor.absorb(other)
    other.delete()

    assert list(stage.trophy_groups.all()) == [kept]


# --- admin -------------------------------------------------------------------------------------------------

def _stage_form(stage, concepts=(), groups=()):
    from trophies.admin import StageAdminForm
    return StageAdminForm(instance=stage, data={
        'series_slug': stage.series_slug, 'stage_number': stage.stage_number, 'title': '',
        'concepts': [c.pk for c in concepts], 'trophy_groups': [g.pk for g in groups],
    })


def test_admin_takes_a_trophy_group():
    concept, _, ctgs = _collection()
    form = _stage_form(StageFactory(series_slug='kh'), groups=[ctgs['001']])
    assert form.is_valid(), form.errors


def test_admin_refuses_a_group_whose_list_is_already_on_the_stage():
    concept, _, ctgs = _collection()
    form = _stage_form(StageFactory(series_slug='kh'), concepts=[concept], groups=[ctgs['001']])
    assert not form.is_valid()
    assert 'trophy_groups' in form.errors


def test_admin_refuses_a_group_whose_list_is_a_bundle_member_on_the_stage():
    concept, _, ctgs = _collection()
    stage = StageFactory(series_slug='kh')
    ConceptBundle.objects.create(stage=stage, label='Set').concepts.add(concept)
    assert not _stage_form(stage, groups=[ctgs['001']]).is_valid()


def test_duplicating_a_stage_carries_its_trophy_groups():
    from trophies.admin import StageAdmin

    _, _, ctgs = _collection()
    stage = StageFactory(series_slug='kh', stage_number=3)
    stage.trophy_groups.add(ctgs['001'])

    created, skipped = StageAdmin._duplicate_stages([stage], 'kh-copy')

    copy = Stage.objects.get(series_slug='kh-copy', stage_number=3)
    assert (created, skipped) == ([3], [])
    assert list(copy.trophy_groups.all()) == [ctgs['001']]


# --- the badge page ------------------------------------------------------------------------------------------

def _ultra_view(series, profile):
    from trophies.services.badge_detail_service import get_badge_detail
    return next(g for g in get_badge_detail(series, profile).groups if g.platform_group.key == 'ultra-hd')


def test_the_badge_page_shows_the_group_with_the_hunters_progress_on_it():
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    profile = ProfileFactory()
    _list_progress(profile, game, 30)
    _progress(profile, game, '001', 100)

    stage = _ultra_view(gb.series, profile).stages[0]
    [entry] = stage['obtainable_games']

    assert entry['group'] == ctgs['001']
    assert entry['pgame'].progress == 100, "the card reads the group's progress, not the list's 30%"
    assert entry['contract'] is None, "the list's contract finishes the list, not this game"
    assert stage['completion_state'] == 'complete' and stage['is_mastered']


def test_my_stats_counts_the_groups_trophies_not_the_whole_lists():
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    profile = ProfileFactory()
    ProfileGame.objects.create(profile=profile, game=game, progress=30, earned_trophies_count=90,
                               earned_trophies={'bronze': 80, 'silver': 9, 'gold': 1, 'platinum': 0})
    ProfileTrophyGroup.objects.create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='001'), progress=100,
        earned_trophies={'bronze': 10, 'silver': 3, 'gold': 1, 'platinum': 0}, last_trophy_at=timezone.now(),
    )

    stats = _ultra_view(gb.series, profile).user_stats

    assert stats['trophies_total'] == 14, 'the collection holds other games; only this group is the badge'
    assert stats['haul'] == {'bronze': 10, 'silver': 3, 'gold': 1, 'platinum': 0}
    assert (stats['games_total'], stats['games_played'], stats['games_hundred']) == (1, 1, 1)
    # "Platted" counts a real platinum: this group has none, as a no-plat list at 100% is never "platted".
    assert (stats['games_platted'], stats['stages_platted'], stats['stages_hundred']) == (0, 0, 1)


def test_a_group_that_holds_a_platinum_counts_as_platted():
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['default']))
    profile = ProfileFactory()
    ProfileTrophyGroup.objects.create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='default'), progress=100,
        earned_trophies={'bronze': 30, 'platinum': 1}, last_trophy_at=timezone.now(),
    )

    stats = _ultra_view(gb.series, profile).user_stats

    assert (stats['games_platted'], stats['stages_platted']) == (1, 1)


def test_a_group_on_a_list_also_shown_whole_counts_as_its_own_game_but_not_its_trophies_twice():
    """Two cards, two games, as the hero's "N games" says; the group's trophies are already in the list's."""
    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']), lambda st: st.concepts.add(concept))
    profile = ProfileFactory()
    ProfileGame.objects.create(profile=profile, game=game, progress=30, earned_trophies_count=20,
                               earned_trophies={'bronze': 20})
    ProfileTrophyGroup.objects.create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='001'), progress=50,
        earned_trophies={'bronze': 5}, last_trophy_at=timezone.now(),
    )

    view = _ultra_view(gb.series, profile)

    assert view.user_stats['trophies_total'] == 20
    assert view.user_stats['games_total'] == 2
    assert view.games_count == 2, 'the hero and My Stats count the same games'


def test_a_group_only_stage_wears_the_groups_icon_not_the_lists_cover():
    """The list HAS real cover art, so this fails if the list's card is allowed to compete for the icon: that
    cover is the whole collection, a different game."""
    concept, game, ctgs = _collection(title_image='https://img.example/collection.png')
    assert game.has_cover_art
    ctgs['001'].icon_url = 'https://img.example/rechain.png'
    ctgs['001'].save()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))

    stage = _ultra_view(gb.series, None).stages[0]

    assert stage['icon_url'] == 'https://img.example/rechain.png'


def test_the_group_card_names_the_group_and_its_list_and_drops_the_lists_plat_chip():
    concept, game, ctgs = _collection(defined_trophies={'platinum': 1})
    html = render_to_string('trophies/partials/badge_detail/badge_detail_items.html',
                            {'game': game, 'pgame': None, 'group': ctgs['001'],
                             'group_name': 'KINGDOM HEARTS Re:Chain of Memories', 'profile': None})

    assert '<p class="bd-gcard__name">KINGDOM HEARTS Re:Chain of Memories</p>' in html
    assert '<p class="bd-gcard__from">Part of Kingdom Hearts Collection</p>' in html
    assert 'Has plat' not in html, "the platinum is the collection's, not this game's"
    assert 'class="bd-gcard__group"' in html and 'Trophy group</span>' in html, 'the art says it is one group'


def test_a_whole_list_card_is_unchanged():
    game = GameFactory(title_name='Kingdom Hearts III', defined_trophies={'platinum': 1})
    html = render_to_string('trophies/partials/badge_detail/badge_detail_items.html',
                            {'game': game, 'pgame': None, 'profile': None})

    assert '<p class="bd-gcard__name">Kingdom Hearts III</p>' in html
    assert 'bd-gcard__from' not in html and 'bd-gcard__group' not in html
    assert 'Has plat' in html


# --- the audit's follow-ups ----------------------------------------------------------------------------------

@pytest.mark.parametrize('whole_catalogue', [False, True])
def test_both_regimes_select_the_groups_versions_and_leave_the_default_read_alone(whole_catalogue):
    """The group read is its own explicit list, so the default-group filter selects exactly what it did."""
    from trophies.models import Game, TrophyGroup

    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))
    catalog = build_catalog([gb], whole_catalogue=whole_catalogue)

    assert set(Game.objects.filter(id__in=catalog['game_filter']).values_list('id', flat=True)) == {game.id}
    assert not TrophyGroup.objects.filter(id__in=catalog['tg_filter']).exists(), \
        'the list is not a whole-list member, so its default group is not read'
    assert catalog['group_tg_ids'] == [game.trophy_groups.get(trophy_group_id='001').id]


def test_the_nightly_statement_never_joins_trophy_groups():
    """`evaluate_badges --all` runs these reads once per profile. Routing group members through the subquery
    joined every concept's trophy groups into all of them."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    concept, game, ctgs = _collection()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']), lambda st: st.concepts.add(ConceptFactory()))
    catalog = build_catalog([gb], whole_catalogue=True)
    profile = ProfileFactory()
    with CaptureQueriesContext(connection) as ctx:
        evaluate_with_catalog(profile, catalog)

    sql = [q['sql'] for q in ctx.captured_queries]
    assert len(sql) == 3, 'the list read, the default-group read and the group read'
    assert not any('concepttrophygroup' in q.lower() for q in sql)


def test_no_group_read_without_a_group_member():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    concept, _, _ = _collection()
    gb = _series(lambda st: st.concepts.add(concept))
    catalog = build_catalog([gb])
    profile = ProfileFactory()
    with CaptureQueriesContext(connection) as ctx:
        evaluate_with_catalog(profile, catalog)

    assert len(ctx.captured_queries) == 2


def test_a_lists_main_group_can_be_a_member_and_is_named_for_its_game():
    """The 'default' CTG is always called "Base Game", so the card takes PSN's own group name instead."""
    concept, game, ctgs = _collection()
    main = game.trophy_groups.get(trophy_group_id='default')
    main.trophy_group_name = 'KINGDOM HEARTS III + Re Mind (DLC)'
    main.save()
    gb = _series(lambda st: st.trophy_groups.add(ctgs['default']))
    profile = ProfileFactory()
    _list_progress(profile, game, 40)
    _progress(profile, game, 'default', 100)

    assert _evaluate(profile, gb).holo_satisfied_count == 1, 'holo is that game at 100%, not the collection'
    [entry] = _ultra_view(gb.series, profile).stages[0]['obtainable_games']
    assert entry['group_name'] == 'KINGDOM HEARTS III + Re Mind (DLC)'


def test_a_delisted_list_retires_its_group_stage_where_delisted_titles_are_excluded():
    _, _, ctgs = _collection(is_delisted=True)
    series = BadgeSeriesFactory()
    StageFactory(series_slug=series.series_slug, stage_number=1).trophy_groups.add(ctgs['001'])
    gb = GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(
        key='ultra-hd-strict', platforms=['PS4', 'PS5'], exclude_delisted=True))

    recompute_required_stages(build_catalog([gb]))
    gb.refresh_from_db()

    assert gb.required_stages == 0


def test_the_card_carries_the_groups_own_ratings():
    from tests.factories import UserConceptRatingFactory

    concept, game, ctgs = _collection()
    UserConceptRatingFactory(concept=concept, concept_trophy_group=ctgs['001'], difficulty=9)
    UserConceptRatingFactory(concept=concept, concept_trophy_group=None, difficulty=2)   # the collection's
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']))

    [entry] = _ultra_view(gb.series, None).stages[0]['obtainable_games']

    assert entry['ratings']['avg_difficulty'] == 9


def test_admin_refuses_a_bundle_holding_a_list_whose_group_is_on_the_stage():
    from django.forms import inlineformset_factory
    from trophies.admin import ConceptBundleInlineFormSet

    concept, _, ctgs = _collection()
    stage = StageFactory(series_slug='kh')
    stage.trophy_groups.add(ctgs['001'])
    FormSet = inlineformset_factory(Stage, ConceptBundle, formset=ConceptBundleInlineFormSet,
                                    fields=('label', 'concepts', 'sort_order'), extra=0)
    formset = FormSet(instance=stage, data={
        'concept_bundles-TOTAL_FORMS': '1', 'concept_bundles-INITIAL_FORMS': '0',
        'concept_bundles-0-label': 'Set', 'concept_bundles-0-concepts': [concept.pk],
        'concept_bundles-0-sort_order': '0',
    })

    assert not formset.is_valid()
    assert 'trophy groups' in str(formset.non_form_errors())


def test_the_stage_generator_reports_a_list_held_through_a_group_instead_of_writing_it():
    from trophies.services.badge_stage_service import _existing_stage_by_origin, origin_key

    concept, _, ctgs = _collection()
    stage = StageFactory(series_slug='kh', stage_number=2)
    stage.trophy_groups.add(ctgs['001'])

    found = _existing_stage_by_origin('kh')

    assert found[origin_key(Concept.objects.get(pk=concept.pk))] == (stage, True)


# --- every call site that asks "which badges does this game count toward?" ---------------------------------

def _live_group_badge(slug='kh-badge'):
    concept, game, ctgs = _collection()
    series = BadgeSeriesFactory(series_slug=slug, name='Kingdom Hearts')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='ultra-hd', platforms=['PS4', 'PS5']))
    StageFactory(series_slug=slug).trophy_groups.add(ctgs['001'])
    return concept, game


def test_the_in_a_badge_browse_filter_keeps_a_list_that_counts_through_a_group():
    from trophies.forms import GameSearchForm
    from trophies.models import Game
    from trophies.views.browse_helpers import apply_game_browse_filters

    _, game = _live_group_badge()
    GameFactory(title_platform=['PS5'])          # in no badge
    form = GameSearchForm({'in_badge': 'on'})
    assert form.is_valid(), form.errors

    qs, _ = apply_game_browse_filters(Game.objects.all(), form)

    assert list(qs) == [game]


def test_the_browse_badge_pills_name_a_badge_reached_through_a_group():
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory
    from trophies.views.game_views import build_game_card_context

    _, game = _live_group_badge()
    request = RequestFactory().get('/games/')
    request.user = AnonymousUser()

    assert build_game_card_context([game], request)['badge_map'][game.concept_id]['names'] == ['Kingdom Hearts']


def test_the_game_page_says_the_list_counts_toward_the_badge():
    from trophies.views.game_views import GameDetailView

    _, game = _live_group_badge()

    assert [b['series_slug'] for b in GameDetailView()._build_badges_context(game)['badges']] == ['kh-badge']


def test_the_company_badge_filter_keeps_a_studio_whose_list_counts_through_a_group(client):
    """Built with the company-list tests' own helpers, which give a game everything that page requires."""
    from django.core.management import call_command
    from django.urls import reverse
    from tests.engine.test_company_list import _company, _link

    studio = _company('Square Enix', 'square-enix')
    concept = _link(studio, 'Kingdom Hearts Collection')
    game = concept.games.get()
    TrophyGroupFactory(game=game, trophy_group_id='001')
    ctg = ConceptTrophyGroupFactory(concept=concept, trophy_group_id='001', display_name='Re:CoM')
    series = BadgeSeriesFactory(series_slug='kh-badge', name='Kingdom Hearts')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='ultra-hd', platforms=['PS4', 'PS5']))
    StageFactory(series_slug='kh-badge').trophy_groups.add(ctg)
    _link(_company('Elsewhere Studio', 'elsewhere-studio'), 'Unrelated Game')
    call_command('recompute_tag_covers', verbosity=0)   # the page reads these counts (see that file's client)

    html = client.get(reverse('companies_list'), {'badge_series': 'kh-badge'}).content.decode()

    assert 'Square Enix' in html
    assert 'Elsewhere Studio' not in html


def test_the_series_runner_and_the_dlc_sweep_reach_hunters_of_a_group_member():
    import inspect
    from trophies.management.commands import detect_dlc_and_refresh
    from trophies.management.commands.evaluate_badges import Command

    concept, game = _live_group_badge()
    profile = ProfileFactory()
    _list_progress(profile, game, 10)

    assert list(Command._series_player_ids('kh-badge')) == [profile.id]
    # The sweep builds its population from the shared helper over a Concept subquery (not OR'd joins through
    # played games), so the path it follows is the one pinned above.
    source = inspect.getsource(detect_dlc_and_refresh)
    assert "played_games__game__concept_id__in=Concept.objects.filter(in_series_q([slug])).values('id')" in source


def test_series_to_groups_conversion_sees_a_group_members_platforms():
    from trophies.management.commands.convert_series_to_groups import _detect_groups

    _live_group_badge()

    spanned, unmapped, count = _detect_groups('kh-badge')

    assert count == 1 and 'ultra-hd' in [g.key for g in spanned]


def test_the_lists_own_platinum_and_contract_are_not_the_stages():
    """The art-reveal count and the job-board coverage ask about the LIST: the collection's platinum is not
    badge work, and its contract does not cover a group's stage."""
    from art_reveal.services import _badge_concept_ids

    concept, _ = _live_group_badge()

    assert concept.id not in set(_badge_concept_ids())
    assert not Concept.objects.filter(in_series_q(['kh-badge'], lists_only=True)).exists()
    assert Concept.objects.filter(in_series_q(['kh-badge'])).get() == concept


def test_the_stage_says_in_words_that_only_the_group_counts():
    """Owner, 2026-10-25: the card alone is easy to read as the whole game, so the stage says it, once per
    group even when several versions of the list carry it."""
    concept, game, ctgs = _collection()
    second = GameFactory(concept=concept, title_name='Kingdom Hearts Collection', title_platform=['PS5'])
    TrophyGroupFactory(game=second, trophy_group_id='001')
    ctgs['001'].display_name = 'Re:Chain of Memories'
    ctgs['001'].save()
    other, _, _ = _collection(title='Kingdom Hearts III')
    gb = _series(lambda st: st.trophy_groups.add(ctgs['001']), lambda st: st.concepts.add(other))
    view = _ultra_view(gb.series, None)

    assert view.stages[0]['group_notes'] == [('Re:Chain of Memories', 'Kingdom Hearts Collection')]
    assert view.stages[1]['group_notes'] == []

    html = render_to_string('trophies/partials/badge_detail/bd2_stage.html', {'s': view.stages[0]})
    assert html.count('class="bd-gnote"') == 1
    assert ('In <strong>Kingdom Hearts Collection</strong>, only the trophies in '
            '<strong>Re:Chain of Memories</strong> count here.') in html
    assert '2 trophy groups</span>' in html, 'the collapsed header says it too (one card per version, as for games)'
    other_html = render_to_string('trophies/partials/badge_detail/bd2_stage.html', {'s': view.stages[1]})
    assert '1 game</span>' in other_html and 'bd-gnote' not in other_html
