"""Excused trophies (`Trophy.is_unobtainable`): finishing a list that can no longer be finished.

The owner's rules (2026-10-24 / 25):

  * earning every trophy that is NOT excused counts as finishing the list, for badges only: stage clears,
    holo and stage XP (contracts, challenges and boards keep PSN's own %)
  * a flagged trophy in the base list also excuses the platinum, which needs every base trophy
  * unobtainable trophies never make a badge harder: a hunter who earned them anyway is unaffected
"""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, ConceptTrophyGroupFactory, EarnedTrophyFactory, GameFactory,
    GroupBadgeFactory, PlatformGroupFactory, ProfileFactory, StageFactory, TrophyFactory, TrophyGroupFactory,
)
from trophies.models import EarnedTrophy, ProfileGame, ProfileTrophyGroup, Trophy, UserGroupBadge
from trophies.services.badge_apply import evaluate_and_apply
from trophies.services.badge_orchestrator import build_catalog, evaluate_with_catalog
from trophies.services.excused_trophies import ExcusedProgress, excused_lists

pytestmark = pytest.mark.django_db

T0 = timezone.now() - timedelta(days=30)


def _list(title='Warhawk', groups=None):
    """A list with trophies per group: {'default': [types], '001': [types]}. Returns (game, {group: [Trophy]})."""
    groups = groups or {'default': ['platinum', 'gold', 'bronze', 'bronze'], '001': ['bronze', 'bronze']}
    concept = ConceptFactory(unified_title=title)
    game = GameFactory(concept=concept, title_name=title, title_platform=['PS5'])
    trophies, n = {}, 0
    for gid, types in groups.items():
        TrophyGroupFactory(game=game, trophy_group_id=gid)
        ConceptTrophyGroupFactory(concept=concept, trophy_group_id=gid, display_name=f'{title} {gid}')
        trophies[gid] = []
        for ttype in types:
            n += 1
            trophies[gid].append(TrophyFactory(game=game, trophy_id=n, trophy_type=ttype, trophy_group_id=gid))
    return game, trophies


def _flag(*trophies):
    Trophy.objects.filter(id__in=[t.id for t in trophies]).update(is_unobtainable=True)


def _earn(profile, *trophies, start=T0):
    for i, t in enumerate(trophies):
        EarnedTrophyFactory(profile=profile, trophy=t, earned_date_time=start + timedelta(days=i))
    return start + timedelta(days=len(trophies) - 1)


def _psn(profile, game, full, base=None):
    """PSN's own percentages, which never reach 100 while a trophy is unearned, and the earned count sync keeps
    beside them. Call after `_earn`."""
    earned = EarnedTrophy.objects.filter(profile=profile, trophy__game=game, earned=True).count()
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={
        'progress': full, 'earned_trophies_count': earned,
    })
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='default'),
        defaults={'progress': base if base is not None else full, 'last_trophy_at': T0},
    )


def _badge(*fills):
    series = BadgeSeriesFactory()
    for number, fill in enumerate(fills, start=1):
        fill(StageFactory(series_slug=series.series_slug, stage_number=number))
    return GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='ultra-hd', platforms=['PS4', 'PS5']))


def _evaluate(profile, gb, whole_catalogue=False):
    return evaluate_with_catalog(profile, build_catalog([gb], whole_catalogue=whole_catalogue))[gb.id]


# --- what is excused --------------------------------------------------------------------------------------

def test_a_list_with_nothing_flagged_is_not_an_excused_list():
    game, _ = _list()

    assert excused_lists([game.id]) == {}


def test_a_flag_in_the_base_list_excuses_the_platinum_too():
    game, t = _list()
    _flag(t['default'][1])          # the gold

    xl = excused_lists([game.id])[game.id]

    assert xl.excused_count('default') == 2, 'the gold, and the platinum that needs it'
    assert xl.required['default'] == frozenset(x.id for x in t['default'][2:])
    assert xl.excused_count() == 2


def test_a_flag_in_a_dlc_group_leaves_the_platinum_required():
    game, t = _list()
    _flag(t['001'][0])

    xl = excused_lists([game.id])[game.id]

    assert t['default'][0].id in xl.required['default'], 'the platinum never needed the DLC'
    assert (xl.excused_count('default'), xl.excused_count('001')) == (0, 1)


def test_a_group_with_every_trophy_flagged_is_dead_not_done():
    """Nothing left to earn is not finishing it: an undated free clear would re-stamp the badge every run."""
    game, t = _list()
    _flag(*t['001'])
    profile = ProfileFactory()
    _earn(profile, *t['default'])

    progress = ExcusedProgress.read(profile, excused_lists([game.id]), [game.id])

    assert progress.group(game.id, '001') == (False, None)
    assert progress.dead(game.id, '001') and not progress.dead(game.id, 'default')
    assert progress.full(game.id)[0], 'the whole list still finishes on the trophies that are left'


def test_a_trophy_group_member_with_every_trophy_flagged_stops_gating():
    """Unobtainable never makes a badge harder: a stage held by a dead group retires, as an unobtainable list's
    stage does, for the required count and for every hunter."""
    from trophies.services.badge_orchestrator import recompute_required_stages

    game, t = _list(title='Kingdom Hearts Collection')
    alive, _ = _list(title='Kingdom Hearts III')
    _flag(*t['001'])
    ctg = game.concept.concept_trophy_groups.get(trophy_group_id='001')
    gb = _badge(lambda st: st.trophy_groups.add(ctg), lambda st: st.concepts.add(alive.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'])
    _psn(profile, game, full=66, base=100)

    catalog = build_catalog([gb])
    recompute_required_stages(catalog)
    gb.refresh_from_db()
    result = evaluate_with_catalog(profile, catalog)[gb.id]

    assert gb.required_stages == 1
    assert (result.gating_count, result.base_satisfied_count) == (1, 0), 'only the live stage is asked for'


def test_a_hunter_short_of_the_smallest_bar_costs_no_read():
    game, t = _list()
    _flag(t['001'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, t['default'][2])                  # one trophy; the smallest bar (the DLC's) needs one...
    _psn(profile, game, full=16, base=25)
    ProfileGame.objects.filter(profile=profile).update(earned_trophies_count=0)   # ...so claim none
    catalog = build_catalog([gb])

    with CaptureQueriesContext(connection) as ctx:
        evaluate_with_catalog(profile, catalog)

    assert len(ctx.captured_queries) == 2


def test_an_excused_earn_keeps_its_date_on_every_run():
    """The undated-clear failure mode: a None earn date re-stamps the hold to "now" on every evaluation."""
    game, t = _list()
    _flag(t['default'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'][2:])
    _psn(profile, game, full=33, base=50)

    evaluate_and_apply(profile)
    first = UserGroupBadge.objects.get(profile=profile, group_badge=gb).earned_at
    evaluate_and_apply(profile)

    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).earned_at == first


def test_an_excused_bundle_member_counts_as_done_on_the_page():
    from trophies.models import ConceptBundle

    game, t = _list(title='Episode 1')
    other, u = _list(title='Episode 2')
    _flag(t['001'][1])

    def fill(st):
        bundle = ConceptBundle.objects.create(stage=st, label='The episodes')
        bundle.concepts.add(game.concept, other.concept)
    gb = _badge(fill)
    profile = ProfileFactory()
    _earn(profile, *t['default'], t['001'][0])
    _psn(profile, game, full=83, base=100)

    [bundle] = _ultra_view(gb.series, profile).stages[0]['bundles']

    # By concept: a bundle's members come off an M2M with no fixed order.
    assert {m['concept'].id: m['done'] for m in bundle['members']} == {game.concept_id: True, other.concept_id: False}
    assert bundle['completed_members'] == 1


def test_sync_does_not_undo_a_flag_set_while_it_ran():
    from types import SimpleNamespace
    from trophies.services.psn_api_service import PsnApiService

    game, t = _list()
    trophy = t['001'][0]
    data = SimpleNamespace(
        trophy_id=trophy.trophy_id, trophy_set_version='01.00', trophy_type=SimpleNamespace(value='bronze'),
        trophy_name='Renamed', trophy_detail='', trophy_icon_url='', trophy_group_id='001',
        trophy_progress_target_value=None, trophy_reward_name=None, trophy_reward_img_url=None,
        trophy_rarity=None, trophy_earn_rate=1.5,
    )
    real_get_or_create = Trophy.objects.get_or_create

    def get_then_flag(*args, **kwargs):
        result = real_get_or_create(*args, **kwargs)
        Trophy.objects.filter(id=trophy.id).update(is_unobtainable=True)    # staff, mid-sync
        return result

    from unittest import mock
    with mock.patch.object(Trophy.objects, 'get_or_create', side_effect=get_then_flag):
        PsnApiService.create_or_update_trophy_from_trophy_data(game, data)

    trophy.refresh_from_db()
    assert (trophy.trophy_name, trophy.is_unobtainable) == ('Renamed', True)


def test_contracts_keep_psns_own_100_percent():
    """Badges only (owner, 2026-10-25): a list finished by excuse is not a contract's 100% tier."""
    from trophies.models import Contract
    from trophies.services.contract_service import _detect_tiers

    game, t = _list()
    _flag(t['001'][1])
    profile = ProfileFactory()
    _earn(profile, *t['default'], t['001'][0])
    _psn(profile, game, full=83, base=100)
    contract = Contract.objects.create(name='Warhawk', slug='warhawk', is_live=True)

    assert _detect_tiers(profile, contract, {game.concept_id}) == (True, False)


# --- admin ------------------------------------------------------------------------------------------------

def test_the_admin_action_flags_and_names_the_series_to_re_run(rf):
    from django.contrib.admin.sites import AdminSite
    from django.contrib.messages.storage.fallback import FallbackStorage
    from trophies.admin import TrophyAdmin

    game, t = _list()
    gb = _badge(lambda st: st.concepts.add(game.concept))
    request = rf.post('/')
    request.session = {}
    request._messages = FallbackStorage(request)
    admin = TrophyAdmin(Trophy, AdminSite())

    admin.mark_trophies_unobtainable(request, Trophy.objects.filter(id=t['001'][1].id))

    assert Trophy.objects.get(id=t['001'][1].id).is_unobtainable
    shown = [str(m) for m in request._messages]
    assert shown[0] == 'Marked 1 trophy(ies) unobtainable.'
    assert f'evaluate_badges --series {gb.series.series_slug}' in shown[1]
    assert set(TrophyAdmin.mark_trophies_unobtainable.allowed_permissions) == {'change'}


def test_reading_no_lists_sends_no_query():
    profile = ProfileFactory()
    with CaptureQueriesContext(connection) as ctx:
        progress = ExcusedProgress.read(profile, {}, [])

    assert len(ctx.captured_queries) == 0
    assert progress.base(1) == (False, None)


def test_progress_is_one_aggregate_read_dated_by_the_last_required_trophy():
    game, t = _list()
    _flag(t['default'][1])
    profile = ProfileFactory()
    last = _earn(profile, *t['default'][2:])
    lists = excused_lists([game.id])

    with CaptureQueriesContext(connection) as ctx:
        progress = ExcusedProgress.read(profile, lists, [game.id])

    assert len(ctx.captured_queries) == 1
    assert progress.base(game.id) == (True, last)
    assert progress.full(game.id) == (False, None), 'the DLC is still unearned'
    assert progress.group(game.id, '009') == (False, None), 'a group the list does not have'


# --- the badge bars ---------------------------------------------------------------------------------------

@pytest.mark.parametrize('whole_catalogue', [False, True])
def test_every_unexcused_trophy_earned_clears_the_stage_and_counts_for_holo(whole_catalogue):
    game, t = _list()
    _flag(t['001'][1])                       # one DLC trophy whose server closed
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'])
    _earn(profile, t['001'][0], start=T0 + timedelta(days=10))
    _psn(profile, game, full=83, base=100)

    result = _evaluate(profile, gb, whole_catalogue)

    assert result.base_satisfied_count == 1
    assert result.holo_satisfied_count == 1, 'the flagged trophy is the only one left, so the list is finished'


def test_without_the_flag_the_same_hunter_is_not_holo():
    game, t = _list()
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'], t['001'][0])
    _psn(profile, game, full=83, base=100)

    assert _evaluate(profile, gb).holo_satisfied_count == 0


def test_a_flagged_base_trophy_lets_the_stage_clear_without_the_platinum():
    """The platinum is excused with it, so the base bar is the rest of the base list, dated by its last trophy."""
    game, t = _list()
    _flag(t['default'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    last = _earn(profile, *t['default'][2:])
    _psn(profile, game, full=33, base=50)

    result = _evaluate(profile, gb)

    assert result.base_satisfied_count == 1
    assert result.holo_satisfied_count == 0, 'the DLC is unearned'
    assert result.earned_date == last


def test_one_required_trophy_short_is_not_finished():
    game, t = _list()
    _flag(t['default'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, t['default'][2])         # one of the two bronzes
    _psn(profile, game, full=16, base=25)

    assert _evaluate(profile, gb).base_satisfied_count == 0


def test_a_trophy_group_member_is_finished_by_its_own_required_trophies():
    game, t = _list(title='Kingdom Hearts Collection')
    _flag(t['001'][1])
    ctg = game.concept.concept_trophy_groups.get(trophy_group_id='001')
    gb = _badge(lambda st: st.trophy_groups.add(ctg))
    profile = ProfileFactory()
    _earn(profile, t['001'][0])
    _psn(profile, game, full=16, base=0)

    result = _evaluate(profile, gb)

    assert (result.base_satisfied_count, result.holo_satisfied_count) == (1, 1)


def test_a_hunter_who_earned_the_flagged_trophy_anyway_is_unaffected_and_costs_no_read():
    game, t = _list()
    _flag(t['001'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'], *t['001'])
    _psn(profile, game, full=100)
    catalog = build_catalog([gb])

    with CaptureQueriesContext(connection) as ctx:
        result = evaluate_with_catalog(profile, catalog)[gb.id]

    assert result.holo_satisfied_count == 1
    assert len(ctx.captured_queries) == 2, 'the list read and the default-group read: no excused read at 100%'


def test_a_hunter_who_never_played_the_flagged_list_costs_no_read():
    game, t = _list()
    _flag(t['001'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    catalog = build_catalog([gb])
    profile = ProfileFactory()

    with CaptureQueriesContext(connection) as ctx:
        evaluate_with_catalog(profile, catalog)

    assert len(ctx.captured_queries) == 2


def test_unflagging_takes_the_badge_back():
    """Holds are binary and live: the flag is staff data, so taking it off undoes what it gave."""
    game, t = _list()
    _flag(t['default'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'][2:])
    _psn(profile, game, full=33, base=50)

    evaluate_and_apply(profile)
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()

    Trophy.objects.filter(game=game).update(is_unobtainable=False)
    evaluate_and_apply(profile)
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()


# --- what the hunter sees ---------------------------------------------------------------------------------

def _ultra_view(series, profile):
    from trophies.services.badge_detail_service import get_badge_detail
    return next(g for g in get_badge_detail(series, profile).groups if g.platform_group.key == 'ultra-hd')


def test_the_badge_page_card_reads_done_and_says_how_many_trophies_were_excused():
    from django.template.loader import render_to_string

    game, t = _list()
    _flag(t['001'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'], t['001'][0])
    _psn(profile, game, full=83, base=100)

    [entry] = _ultra_view(gb.series, profile).stages[0]['obtainable_games']
    assert (entry['excused_count'], entry['excused_done']) == (1, True)

    html = render_to_string('trophies/partials/badge_detail/badge_detail_items.html', {
        'game': game, 'pgame': entry['pgame'], 'excused_count': 1, 'excused_done': True, 'profile': None,
    })
    assert 'bd-gcard is-done' in html, 'finished for the badge, though PSN reads 83%'
    assert '<span class="bd-gcard__pct is-done">83%</span>' in html, "the % stays PSN's own"
    assert '1 excused<span class="sr-only">: unobtainable, not needed for this badge</span>' in html


def test_an_anonymous_viewer_sees_the_count_but_nothing_is_done():
    game, t = _list()
    _flag(t['001'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))

    [entry] = _ultra_view(gb.series, None).stages[0]['obtainable_games']

    assert (entry['excused_count'], entry['excused_done']) == (1, False)


def test_a_card_on_a_list_with_nothing_excused_is_unchanged():
    game, t = _list()
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'])
    _psn(profile, game, full=66, base=100)

    [entry] = _ultra_view(gb.series, profile).stages[0]['obtainable_games']

    assert (entry['excused_count'], entry['excused_done']) == (0, False)


def test_a_group_card_counts_only_its_own_groups_excused_trophies():
    game, t = _list(title='Kingdom Hearts Collection')
    _flag(t['default'][1], t['001'][1])
    ctg = game.concept.concept_trophy_groups.get(trophy_group_id='001')
    gb = _badge(lambda st: st.trophy_groups.add(ctg))
    profile = ProfileFactory()
    _earn(profile, t['001'][0])
    _psn(profile, game, full=16, base=0)

    [entry] = _ultra_view(gb.series, profile).stages[0]['obtainable_games']

    assert (entry['excused_count'], entry['excused_done']) == (1, True), 'not the base list gold or the platinum'


def test_the_game_pages_trophy_rows_mark_the_excused_trophies_including_the_platinum():
    from django.template.loader import render_to_string
    from trophies.views.game_views import build_trophy_rows, group_trophy_rows

    game, t = _list()
    _flag(t['default'][1])

    rows, _ = build_trophy_rows(game)

    flagged = {r['trophy_id'] for r in rows if r['is_unobtainable']}
    assert flagged == {t['default'][0].trophy_id, t['default'][1].trophy_id}, 'the gold, and the platinum with it'
    html = render_to_string('trophies/partials/trophy_grid/trophy_list.html', {
        'trophies': group_trophy_rows(rows)['default'], 'earned': {}, 'profile': None, 'id_prefix': '',
    })
    assert html.count('class="gd-trophy__unob"') == 2


def test_a_card_whose_platinum_is_excused_stops_offering_it():
    from django.template.loader import render_to_string

    game, t = _list()
    game.defined_trophies = {'platinum': 1, 'gold': 1, 'bronze': 2}
    game.save()
    _flag(t['default'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))

    [entry] = _ultra_view(gb.series, None).stages[0]['obtainable_games']
    html = render_to_string('trophies/partials/badge_detail/badge_detail_items.html', {
        'game': game, 'pgame': None, 'excused_count': entry['excused_count'], 'plat_excused': entry['plat_excused'],
        'profile': None,
    })

    assert entry['plat_excused'] and entry['excused_count'] == 2
    assert 'Plat unobtainable</span>' in html and 'Has plat' not in html


def _version(game, platform, groups=None):
    """Another version of `game`'s list (same concept), with its own trophies."""
    groups = groups or {'default': ['platinum', 'gold', 'bronze', 'bronze'], '001': ['bronze', 'bronze']}
    other = GameFactory(concept=game.concept, title_name=game.title_name, title_platform=[platform])
    trophies, n = {}, 0
    for gid, types in groups.items():
        TrophyGroupFactory(game=other, trophy_group_id=gid)
        trophies[gid] = []
        for ttype in types:
            n += 1
            trophies[gid].append(TrophyFactory(game=other, trophy_id=n, trophy_type=ttype, trophy_group_id=gid))
    return other, trophies


def test_a_dead_base_list_finished_through_its_dlc_is_dated_and_stays_dated():
    """Every base trophy flagged, the DLC alive and earned: the full bar alone carries the clear, so it must carry
    a date too, or the badge re-stamps every run. A live second version keeps the stage gating."""
    game, t = _list()
    _version(game, 'PS4')
    _flag(*t['default'])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    last = _earn(profile, *t['001'])
    _psn(profile, game, full=33, base=0)

    result = _evaluate(profile, gb)
    assert (result.gating_count, result.base_satisfied_count) == (1, 1)
    assert result.earned_date == last

    evaluate_and_apply(profile)
    first = UserGroupBadge.objects.get(profile=profile, group_badge=gb).earned_at
    evaluate_and_apply(profile)
    assert UserGroupBadge.objects.get(profile=profile, group_badge=gb).earned_at == first


def test_a_flag_on_one_version_leaves_the_other_version_required():
    game, t = _list()
    ps4, u = _version(game, 'PS4')
    _flag(t['default'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *[x for x in u['default'] if x.trophy_type != 'platinum' and x != u['default'][1]])
    _psn(profile, ps4, full=33, base=50)

    assert _evaluate(profile, gb).base_satisfied_count == 0, "the PS4 list's gold is alive: no excuse there"


def test_a_dead_version_files_with_the_unobtainable_ones_on_the_page():
    """The page files a version by the engine's own rule: a group with every trophy excused cannot be finished."""
    game, t = _list(title='Kingdom Hearts Collection')
    alive, _ = _list(title='Kingdom Hearts Re:Chain (PS4)')
    _flag(*t['001'])
    ctg = game.concept.concept_trophy_groups.get(trophy_group_id='001')

    def fill(st):
        st.concepts.add(alive.concept)
        st.trophy_groups.add(ctg)
    stage = _ultra_view(_badge(fill).series, None).stages[0]

    assert [e['game'] for e in stage['obtainable_games']] == [alive]
    assert [e['game'] for e in stage['delisted_games']] == [game]


def test_a_hunter_who_earned_the_platinum_before_it_died_is_not_told_it_is_unobtainable():
    from django.template.loader import render_to_string

    game, t = _list()
    game.defined_trophies = {'platinum': 1, 'gold': 1, 'bronze': 2}
    game.save()
    pgame = ProfileGame(progress=100, earned_trophies={'platinum': 1})
    html = render_to_string('trophies/partials/badge_detail/badge_detail_items.html', {
        'game': game, 'pgame': pgame, 'excused_count': 2, 'plat_excused': True, 'profile': None,
    })

    assert 'Plat unobtainable' not in html and 'Plat</span>' in html


def test_the_roadmap_hint_finds_ticks_staff_have_not_confirmed(rf):
    from django.contrib.admin.sites import AdminSite
    from trophies.admin import RoadmapSaysUnobtainableFilter, TrophyAdmin
    from trophies.models import Roadmap, TrophyGuide

    game, t = _list()
    ctg = game.concept.concept_trophy_groups.get(trophy_group_id='default')
    roadmap = Roadmap.objects.create(concept=game.concept, concept_trophy_group=ctg)
    for trophy in t['default'][1:3]:
        TrophyGuide.objects.create(roadmap=roadmap, trophy_id=trophy.trophy_id, body='', is_unobtainable=True)
    _flag(t['default'][1])
    admin = TrophyAdmin(Trophy, AdminSite())
    qs = admin.get_queryset(rf.get('/')).filter(game=game)

    def pick(value):
        f = RoadmapSaysUnobtainableFilter(rf.get('/'), {'roadmap_unobtainable': [value]}, Trophy, admin)
        return {x.id for x in f.queryset(None, qs)}

    assert pick('yes') == {t['default'][1].id, t['default'][2].id}
    assert pick('unconfirmed') == {t['default'][2].id}


def test_the_badge_page_reads_a_viewers_excused_trophies_once():
    """The page shares its read with the evaluation rather than sending the same aggregate twice."""
    game, t = _list()
    _flag(t['001'][1])
    gb = _badge(lambda st: st.concepts.add(game.concept))
    profile = ProfileFactory()
    _earn(profile, *t['default'], t['001'][0])
    _psn(profile, game, full=83, base=100)

    with CaptureQueriesContext(connection) as ctx:
        _ultra_view(gb.series, profile)

    reads = [q['sql'] for q in ctx.captured_queries
             if 'MAX("trophies_earnedtrophy"."earned_date_time")' in q['sql'] and 'GROUP BY' in q['sql']]
    assert len(reads) == 1


# --- the excused row and its banner (owner, 2026-10-25) ---------------------------------------------------

def _stage_html(stage):
    from django.template.loader import render_to_string
    return render_to_string('trophies/partials/badge_detail/bd2_stage.html', {'s': stage})


def test_lists_with_excused_trophies_get_their_own_row_under_the_full_lists():
    game, t = _list(title='Warhawk')
    full, _ = _list(title='Starhawk')
    _flag(t['001'][1])

    def fill(st):
        st.concepts.add(full.concept, game.concept)
    stage = _ultra_view(_badge(fill).series, None).stages[0]

    assert [e['game'] for e in stage['list_games']] == [full]
    assert [e['game'] for e in stage['excused_games']] == [game]
    html = _stage_html(stage)
    assert html.index('Starhawk') < html.index('class="bd-groups"') < html.index('bd-gnote--excused')
    assert ('In <strong>Warhawk</strong> (PS5), 1 DLC trophy can no longer be earned, so it isn&rsquo;t needed '
            'here.</span>') in html


@pytest.mark.parametrize('flags, sentence', [
    ({'default': [1]}, '1 base-game trophy can no longer be earned, so it isn&rsquo;t needed here. '
                       'The platinum needs it, so it isn&rsquo;t needed either.'),
    ({'001': [0, 1]}, '2 DLC trophies can no longer be earned, so they aren&rsquo;t needed here.</span>'),
    ({'default': [1], '001': [0]}, '1 base-game and 1 DLC trophies can no longer be earned, so they aren&rsquo;t '
                                   'needed here. The platinum needs them, so it isn&rsquo;t needed either.'),
    ({'default': [0]}, 'the platinum can no longer be earned, so it isn&rsquo;t needed here.</span>'),
])
def test_the_banner_says_whether_the_excused_trophies_are_base_game_dlc_or_both(flags, sentence):
    game, t = _list()
    _flag(*[t[gid][i] for gid, idx in flags.items() for i in idx])
    stage = _ultra_view(_badge(lambda st: st.concepts.add(game.concept)).series, None).stages[0]

    html = _stage_html(stage)

    assert f'In <strong>Warhawk</strong> (PS5), {sentence}' in html


def test_two_versions_flagged_alike_say_it_once():
    game, t = _list()
    ps4, u = _version(game, 'PS5')
    _flag(t['001'][1], u['001'][1])
    stage = _ultra_view(_badge(lambda st: st.concepts.add(game.concept)).series, None).stages[0]

    assert len(stage['excused_games']) == 2 and len(stage['excused_notes']) == 1


def test_a_trophy_group_with_excused_trophies_says_so_in_the_group_row():
    game, t = _list(title='Kingdom Hearts Collection')
    _flag(t['001'][1])
    ctg = game.concept.concept_trophy_groups.get(trophy_group_id='001')
    stage = _ultra_view(_badge(lambda st: st.trophy_groups.add(ctg)).series, None).stages[0]

    html = _stage_html(stage)

    assert stage['excused_games'] == [] and len(stage['group_excused_notes']) == 1
    assert ('In <strong>Kingdom Hearts Collection</strong> (PS5), 1 trophy in <strong>Kingdom Hearts Collection '
            '001 (DLC)</strong> can no longer be earned') in html
    assert 'class="bd-groups bd-groups--lead"' in html


def test_a_stage_with_nothing_excused_has_no_excused_row():
    game, _ = _list()
    stage = _ultra_view(_badge(lambda st: st.concepts.add(game.concept)).series, None).stages[0]

    assert (stage['excused_games'], stage['excused_notes']) == ([], [])
    assert 'bd-gnote--excused' not in _stage_html(stage)
