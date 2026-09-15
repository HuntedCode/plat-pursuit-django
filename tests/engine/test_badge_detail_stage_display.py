"""How the badge-detail journey PRESENTS stages, after cross-platform satisfaction landed.

Three owner-decided display rules, plus the display fallout the engine change created:

1. Stages are numbered 1..N over the REQUIRED ladder, never by their authored `stage_number`. Authored
   numbers have gaps -- stages get retired, renumbered, authored out of order -- and "Stage 1, Stage 2,
   Stage 5" sends the reader hunting for the missing ones.
2. A stage that is in scope but no longer GATES (everything it asks for on this edition's platforms has gone
   unobtainable) drops out of the ladder into a clearly-fenced section at the bottom, with no number. It is
   kept rather than dropped because clearing it still PAID.
3. An edition with nothing left to gate says so, once, at the top -- and stops advertising a stage count and
   a points offer for a chase that no longer exists.

The suite had NO fixture with a stage spanning two platforms before this file, which is why every one of
these bugs was invisible: the engine change only affects stages that overlap editions.
"""
import pytest

from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory,
    PlatformGroupFactory, ProfileFactory, StageFactory,
)
from trophies.models import ProfileGame, ProfileTrophyGroup, TrophyGroup
from trophies.services.badge_detail_service import get_badge_detail
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _ultra():
    return PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'],
                                exclude_delisted=True)


def _legacy():
    return PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS3'],
                                exclude_delisted=False)


def _complete(profile, game):
    """Clear a game's default trophy group and NOTHING more -- the BASE bar, without the holo one.

    `progress` deliberately short of 100: this used to set it to 100, making `_complete` byte-identical to
    `_hundred` below, so every retired / cross-platform test silently exercised a 100% clear and the
    platted-but-not-maxed path had no coverage outside the two tests named for it.
    """
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 72})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default',
                                              defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg,
        defaults={'progress': 100, 'last_trophy_at': timezone.now()})


def _stage(slug, number, *, title='', platforms_obtainable=(), platforms_dead=()):
    """One stage holding a game per platform, split into obtainable and unobtainable."""
    st = StageFactory(series_slug=slug, stage_number=number, title=title)
    concept = ConceptFactory()
    st.concepts.add(concept)
    made = {}
    for plat in platforms_obtainable:
        made[plat] = GameFactory(concept=concept, title_platform=[plat], is_obtainable=True)
    for plat in platforms_dead:
        made[plat] = GameFactory(concept=concept, title_platform=[plat], is_obtainable=False)
    return st, made


def _view(series, profile, key='ultra-hd'):
    detail = get_badge_detail(series, profile)
    return next(g for g in detail.groups if g.platform_group.key == key)


# ── 1. renumbering ───────────────────────────────────────────────────────────────────────────────

def test_stages_are_numbered_from_one_whatever_the_authored_numbers_are():
    """Authored 3, 7, 11 -> displayed 1, 2, 3."""
    series = BadgeSeriesFactory(series_slug='gaps')
    for n in (3, 7, 11):
        _stage('gaps', n, title=f'Authored {n}', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert [s['display_number'] for s in g.stages] == [1, 2, 3]
    assert [s['stage'].stage_number for s in g.stages] == [3, 7, 11], 'authored numbers should be intact'


def test_renumbering_closes_the_gap_a_retired_stage_leaves():
    """THE case the renumbering is for: pull the middle stage out of the ladder and the rest must read
    1, 2 -- not 1, 3, which invites a hunt for the stage that is not missing, just not required."""
    series = BadgeSeriesFactory(series_slug='gap2')
    _stage('gap2', 1, title='Alive', platforms_obtainable=('PS5',))
    _stage('gap2', 2, title='Dead', platforms_dead=('PS5',))
    _stage('gap2', 3, title='Also alive', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert [s['display_number'] for s in g.stages] == [1, 2]
    assert [s['stage'].title for s in g.stages] == ['Alive', 'Also alive']


# ── 2. retired stages ────────────────────────────────────────────────────────────────────────────

def test_an_unobtainable_stage_leaves_the_ladder_for_the_retired_section():
    series = BadgeSeriesFactory(series_slug='ret')
    _stage('ret', 1, title='Alive', platforms_obtainable=('PS5',))
    _stage('ret', 2, title='Dead', platforms_dead=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert [s['stage'].title for s in g.stages] == ['Alive']
    assert [s['stage'].title for s in g.retired_stages] == ['Dead']
    assert g.gating_count == 1


def test_a_retired_stage_carries_no_number():
    """A number reads as 'step N of this badge'. These are not steps -- the section says so once."""
    series = BadgeSeriesFactory(series_slug='ret2')
    _stage('ret2', 1, title='Alive', platforms_obtainable=('PS5',))
    _stage('ret2', 2, title='Dead', platforms_dead=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert g.retired_stages[0]['display_number'] is None


def test_a_retired_stage_the_hunter_cleared_still_shows_as_complete():
    """It paid -- the page must not take the acknowledgement back just because the stage stopped counting."""
    series = BadgeSeriesFactory(series_slug='ret3')
    _stage('ret3', 1, title='Alive', platforms_obtainable=('PS5',))
    _dead_stage, games = _stage('ret3', 2, title='Dead', platforms_dead=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    profile = ProfileFactory()
    _complete(profile, games['PS5'])

    g = _view(series, profile)

    assert g.retired_stages[0]['completion_state'] == 'complete'


def test_a_stage_off_this_editions_platforms_is_neither_required_nor_retired():
    """Out of scope is not the same as retired. A PS3-only stage credits an Ultra HD badge nothing, so it
    does not belong on that edition's page at all -- putting it in the retired section would imply it was
    once part of this badge."""
    series = BadgeSeriesFactory(series_slug='scope')
    _stage('scope', 1, title='On edition', platforms_obtainable=('PS5',))
    _stage('scope', 2, title='Elsewhere', platforms_obtainable=('PS3',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    titles = [s['stage'].title for s in g.stages] + [s['stage'].title for s in g.retired_stages]
    assert 'Elsewhere' not in titles


# ── 3. unearnable editions ───────────────────────────────────────────────────────────────────────

def test_an_edition_with_nothing_obtainable_is_flagged_unearnable():
    series = BadgeSeriesFactory(series_slug='dead')
    _stage('dead', 1, title='Gone', platforms_dead=('PS5',))
    _stage('dead', 2, title='Also gone', platforms_dead=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert g.is_unearnable is True
    assert g.stages == []
    assert len(g.retired_stages) == 2


def test_an_unearnable_edition_offers_no_points():
    """'600 Points on offer' beside a badge nobody can earn is the page arguing with its own banner."""
    series = BadgeSeriesFactory(series_slug='dead2')
    _stage('dead2', 1, title='Gone', platforms_dead=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert g.xp_on_offer == 0


def test_a_live_edition_is_not_flagged():
    series = BadgeSeriesFactory(series_slug='live')
    _stage('live', 1, title='Alive', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    assert _view(series, ProfileFactory()).is_unearnable is False


# ── the points offer ─────────────────────────────────────────────────────────────────────────────

def test_the_offer_prices_every_in_scope_stage_not_just_the_gating_ones():
    """XP pays per in-scope stage cleared, so an offer priced off `gating_count` under-reports a badge
    holding a retired stage -- the page would promise less than the badge actually pays."""
    from trophies.services.badge_xp import XP_BADGE_COMPLETION_BONUS, XP_PER_STAGE

    series = BadgeSeriesFactory(series_slug='offer')
    _stage('offer', 1, title='Alive', platforms_obtainable=('PS5',))
    _stage('offer', 2, title='Dead', platforms_dead=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert g.gating_count == 1
    assert g.xp_on_offer == 2 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS


# ── cross-platform display ───────────────────────────────────────────────────────────────────────

def test_a_stage_lists_every_version_not_just_this_editions():
    """Owner's call: show all versions. Filtering to the edition's platforms left a ticked stage whose
    visible games were all untouched -- the check was earned by a list the page refused to show."""
    series = BadgeSeriesFactory(series_slug='xplat')
    _st, games = _stage('xplat', 1, title='Cross-gen', platforms_obtainable=('PS5', 'PS3'))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    GroupBadgeFactory(series=series, platform_group=_legacy(), is_live=True)
    profile = ProfileFactory()
    _complete(profile, games['PS3'])

    g = _view(series, profile, key='ultra-hd')
    listed = {e['game'].id for s in g.stages for e in s['obtainable_games'] + s['delisted_games']}

    assert g.stages[0]['completion_state'] == 'complete', 'the PS3 clear did not credit Ultra HD'
    assert games['PS3'].id in listed, 'the game that earned the check was not shown'
    assert games['PS5'].id in listed


def test_my_stats_counts_the_games_the_page_shows():
    """The panel and the hero must describe the same badge. Platform-scoping My Stats against a
    cross-platform hero produced a mastered badge whose stats panel read all zeros."""
    series = BadgeSeriesFactory(series_slug='stats')
    _st, games = _stage('stats', 1, title='Cross-gen', platforms_obtainable=('PS5', 'PS3'))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    profile = ProfileFactory()
    _hundred(profile, games['PS3'])

    g = _view(series, profile)

    assert g.stages_cleared == 1
    assert g.user_stats['games_hundred'] >= 1, (
        'My Stats reported nothing for a stage the hero says is cleared'
    )


# ── the two implementations of one rule ──────────────────────────────────────────────────────────

def test_journey_required_set_matches_the_engine():
    """`_stage_gates_for_group` mirrors the engine so an ANONYMOUS visitor (who gets no evaluation) can
    still be told which stages are required. Two implementations of one rule drift silently, so this pins
    them against each other on a catalogue with every awkward shape: alive, dead-here-alive-elsewhere,
    off-edition, and delisted-in-an-excluding-group.
    """
    series = BadgeSeriesFactory(series_slug='mirror')
    _stage('mirror', 1, title='Alive here', platforms_obtainable=('PS5',))
    _stage('mirror', 2, title='Dead here, alive on PS3', platforms_obtainable=('PS3',),
           platforms_dead=('PS5',))
    _stage('mirror', 3, title='Off edition', platforms_obtainable=('PS3',))
    st4 = StageFactory(series_slug='mirror', stage_number=4, title='Delisted here')
    c4 = ConceptFactory()
    st4.concepts.add(c4)
    GameFactory(concept=c4, title_platform=['PS5'], is_obtainable=True, is_delisted=True)
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    anon = _view(series, None)             # no profile -> the mirror decides
    signed_in = _view(series, ProfileFactory())   # engine result decides

    assert [s['stage'].stage_number for s in anon.stages] == \
           [s['stage'].stage_number for s in signed_in.stages], 'the mirror disagrees with the engine'
    assert [s['stage'].stage_number for s in anon.retired_stages] == \
           [s['stage'].stage_number for s in signed_in.retired_stages]
    # ...and the split is the one the rules describe: only stage 1 can be required of an Ultra HD hunter.
    assert [s['stage'].stage_number for s in signed_in.stages] == [1]
    assert [s['stage'].stage_number for s in signed_in.retired_stages] == [2, 4]


# ── platinum vs 100% ─────────────────────────────────────────────────────────────────────────────

def _hundred(profile, game):
    """100% INCLUDING DLC -- the holo bar. ProfileGame.progress == 100 is what the engine reads for it."""
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 100})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default',
                                              defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg,
        defaults={'progress': 100, 'last_trophy_at': timezone.now()})


def _platted_only(profile, game):
    """The BASE bar without the holo one: default trophy group at 100%, whole game short of it."""
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 72})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default',
                                              defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg,
        defaults={'progress': 100, 'last_trophy_at': timezone.now()})


def test_a_platted_stage_is_not_marked_mastered():
    """The distinction the page was missing: cleared and 100%'d rendered identically, so the badge asked
    for 100% (it is what makes it holographic) and never acknowledged it."""
    series = BadgeSeriesFactory(series_slug='bar1')
    _st, games = _stage('bar1', 1, title='Platted', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    profile = ProfileFactory()
    _platted_only(profile, games['PS5'])

    g = _view(series, profile)

    assert g.stages[0]['completion_state'] == 'complete', 'the platinum should still clear the stage'
    assert g.stages[0]['is_mastered'] is False


def test_a_hundred_percent_stage_is_marked_mastered():
    series = BadgeSeriesFactory(series_slug='bar2')
    _st, games = _stage('bar2', 1, title='Maxed', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    profile = ProfileFactory()
    _hundred(profile, games['PS5'])

    g = _view(series, profile)

    assert g.stages[0]['completion_state'] == 'complete'
    assert g.stages[0]['is_mastered'] is True


def test_mastery_does_not_change_what_earns_the_badge():
    """`completion_state` stays three-valued on purpose: base completion is what earns the badge, and
    everything downstream keying on 'complete' must keep meaning exactly that. Mastery rides alongside."""
    series = BadgeSeriesFactory(series_slug='bar3')
    _st, games = _stage('bar3', 1, title='Platted', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    profile = ProfileFactory()
    _platted_only(profile, games['PS5'])

    g = _view(series, profile)

    # `state` is deliberately NOT asserted: it reads the stored UserGroupBadge hold, and this test never
    # runs an apply pass. What matters here is that the platinum counts toward the badge while the holo
    # bar stays unmet -- the two counters moving independently is the whole point.
    assert g.stages_cleared == 1, 'the platinum must still count toward the badge'
    assert g.holo_satisfied_count == 0, 'a platinum should not satisfy the 100% bar'


def test_mastery_crosses_platforms_like_completion_does():
    """Holo follows base across editions (owner's call), so 100%-ing the PS3 list marks the Ultra HD
    stage mastered too. Pinned here as well as in the engine because THIS is where a hunter sees it."""
    series = BadgeSeriesFactory(series_slug='bar4')
    _st, games = _stage('bar4', 1, title='Cross-gen', platforms_obtainable=('PS5', 'PS3'))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    profile = ProfileFactory()
    _hundred(profile, games['PS3'])

    g = _view(series, profile, key='ultra-hd')

    assert g.stages[0]['is_mastered'] is True


def test_an_anonymous_visitor_sees_no_mastery_claims():
    """No profile, no evaluation, so nothing is complete OR mastered -- the marker must not default on."""
    series = BadgeSeriesFactory(series_slug='bar5')
    _stage('bar5', 1, title='Anon', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, None)

    assert g.stages[0]['is_mastered'] is False
    assert g.stages[0]['completion_state'] == 'todo'


def test_the_summary_tiles_and_the_ladder_tell_the_same_story(client):
    """The requirements strip is a SUMMARY of the ladder below it, so the two must not disagree about
    which bar a stage reached.

    They did: the strip's `.is-done` rule paints the tile background and the check pill success-green, and
    the first attempt at distinguishing them overrode only `border-color` -- leaving a green tile with a
    green pill and a mis-tinted glyph, which read as "100%" for a stage that was merely platted.

    Rendered rather than unit-tested because the disagreement was ENTIRELY in the markup: the service had
    `is_mastered` right all along and both surfaces read the same flag.
    """
    from tests.factories import UserFactory
    import re

    series = BadgeSeriesFactory(series_slug='sum1', name='Summary Series')
    _st1, platted = _stage('sum1', 1, title='Platted stage', platforms_obtainable=('PS5',))
    _st2, maxed = _stage('sum1', 2, title='Maxed stage', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    user = UserFactory()
    profile = ProfileFactory(user=user, is_linked=True)
    _platted_only(profile, platted['PS5'])
    _hundred(profile, maxed['PS5'])
    client.force_login(user)

    html = client.get(f'/badges/{series.series_slug}/').content.decode()

    # Scoped to each tile's own element: the page carries the same words in the ladder below, so a bare
    # substring search would pass on the ladder's markup while the strip stayed wrong.
    tiles = re.findall(r'<a[^>]*class="bd-req__tile[^"]*"[^>]*>.*?</a>', html, re.S)
    assert len(tiles) == 2, f'expected two summary tiles, found {len(tiles)}'
    platted_tile = next(t for t in tiles if 'Platted stage' in t)
    maxed_tile = next(t for t in tiles if 'Maxed stage' in t)

    assert 'is-mastered' not in platted_tile, 'a platted stage was marked 100% in the summary'
    assert 'is-mastered' in maxed_tile, 'a 100% stage was not marked in the summary'
    assert 'bd-req__tile-bar--max' not in platted_tile
    assert 'bd-req__tile-bar--max' in maxed_tile
    # ...and in text, for anyone not reading colour.
    assert 'completed, not yet 100%' in platted_tile
    assert 'completed at 100%' in maxed_tile


# ── rendered output ──────────────────────────────────────────────────────────────────────────────

def _render(client, series, profile=None):
    if profile is not None:
        client.force_login(profile.user)
    resp = client.get(f'/badges/{series.series_slug}/')
    assert resp.status_code == 200
    return resp.content.decode()


def test_the_stage_header_counts_the_games_it_lists(client):
    """`{{ a|length|add:b|length }}` is NOT valid Django -- a filter argument cannot itself be filtered. It
    raised internally, evaluated to the empty string, and `|length` of that is 0, so EVERY stage on the page
    rendered "0 games". Two independent audits found it; no test did, because nothing here rendered the
    template. The count lives in the service now."""
    import re

    series = BadgeSeriesFactory(series_slug='count1')
    _stage('count1', 1, title='Three versions', platforms_obtainable=('PS5', 'PS4', 'PS3'))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    html = _render(client, series)

    counts = re.findall(r'<span class="bd-stage__count">([^<]+)</span>', html)
    assert counts == ['3 games'], f'stage header rendered {counts}'


def test_a_single_game_stage_says_one_game_not_zero(client):
    import re

    series = BadgeSeriesFactory(series_slug='count2')
    _stage('count2', 1, title='One version', platforms_obtainable=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    html = _render(client, series)

    assert re.findall(r'<span class="bd-stage__count">([^<]+)</span>', html) == ['1 game']


def test_an_anonymous_visitor_is_not_told_a_live_badge_is_dead(client):
    """`required_stages` is written only by `evaluate_badges`, so a freshly-authored badge sits at its
    default of 0 until the nightly. Reading the denorm for anon put the banner "This badge can no longer be
    earned. Every game it asks for has become unobtainable" directly above two numbered, fully obtainable
    stages -- for every visitor and every crawler, after every authoring session. Anon reads the ladder."""
    series = BadgeSeriesFactory(series_slug='fresh')
    _stage('fresh', 1, title='Alive one', platforms_obtainable=('PS5',))
    _stage('fresh', 2, title='Alive two', platforms_obtainable=('PS5',))
    gb = GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    assert gb.required_stages == 0, 'fixture must model the un-swept denorm'

    g = _view(series, None)
    html = _render(client, series)

    assert g.gating_count == 2, 'anon fell back to the stale denorm'
    assert g.is_unearnable is False
    assert 'bd-dead' not in html, 'a live badge showed the unearnable banner to an anonymous visitor'


def test_an_unearnable_edition_says_it_once(client):
    """The banner and the requirements card both announced it, and the card's tile grid loops the (empty)
    required ladder -- so the second announcement came with a gap under it."""
    series = BadgeSeriesFactory(series_slug='once')
    _stage('once', 1, title='Gone', platforms_dead=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    html = _render(client, series)

    assert html.count('bd-dead__title') == 1
    assert 'bd-req__ask' not in html, 'the requirements card rendered alongside the banner'
    # NOT `'No longer earnable' not in html`: that string is also the HERO's state label, which is
    # deliberate and correct. Scoped to the requirements card's own element instead -- the duplication
    # that mattered was the card repeating the banner, not the hero naming the state.
    assert 'bd-req__grid' not in html, 'the empty tile grid rendered under the banner'


def test_an_unearnable_edition_renders_no_progressbar(client):
    """With zero gating stages the track had no segments and announced "0 of 0 stages" to a screen reader --
    a progressbar for a chase the paragraph below says is over."""
    series = BadgeSeriesFactory(series_slug='nobar')
    _stage('nobar', 1, title='Gone', platforms_dead=('PS5',))
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    html = _render(client, series)

    assert 'role="progressbar"' not in html
    assert 'No longer earnable' in html


# ── bundles: the shapes the deleted mirror got wrong ─────────────────────────────────────────────

def _bundle_stage(slug, number, members, *, title=''):
    """A stage whose only qualifier is a ConceptBundle. `members` is a list of (platform, obtainable,
    delisted) tuples, one concept each -- the bundle needs EVERY member, so its platforms are their
    INTERSECTION and it is obtainable only where all of them are."""
    from trophies.models import ConceptBundle

    st = StageFactory(series_slug=slug, stage_number=number, title=title)
    bundle = ConceptBundle.objects.create(stage=st, label=title or 'Episodic')
    concepts = []
    for plat, obtainable, delisted in members:
        c = ConceptFactory()
        GameFactory(concept=c, title_platform=[plat], is_obtainable=obtainable, is_delisted=delisted)
        concepts.append(c)
    bundle.concepts.set(concepts)
    return st


def test_a_bundle_needing_two_platforms_gates_neither_edition():
    """The mirror's worst drift. A bundle needs every member, so one member on PS3 and one on PS5 can be
    completed on NEITHER edition -- its platforms are the intersection, which is empty. The hand-written
    mirror unioned them and made it a live requirement of both."""
    series = BadgeSeriesFactory(series_slug='bun1')
    _bundle_stage('bun1', 1, [('PS3', True, False), ('PS5', True, False)], title='Split era')
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)
    GroupBadgeFactory(series=series, platform_group=_legacy(), is_live=True)

    ultra = _view(series, ProfileFactory(), key='ultra-hd')
    legacy = _view(series, ProfileFactory(), key='legacy-hd')

    assert ultra.gating_count == 0 and legacy.gating_count == 0
    assert ultra.stages == [] and legacy.stages == []


def test_a_bundle_member_with_a_delisted_sibling_retires_on_an_excluding_edition():
    """`_bundle_state` takes `delisted = ANY game of ANY member`, so one delisted SKU retires the whole
    bundle on Ultra HD (which excludes delisted). The mirror asked "does this member have a playable game"
    per member and kept it required. A cross-gen concept with a delisted SKU is the normal case."""
    series = BadgeSeriesFactory(series_slug='bun2')
    _bundle_stage('bun2', 1, [('PS5', True, True), ('PS5', True, False)], title='One delisted')
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert g.gating_count == 0, 'a delisted member still gated an excluding edition'
    assert [s['stage'].title for s in g.retired_stages] == ['One delisted']


def test_a_bundle_with_a_gameless_member_cannot_gate():
    """`_bundle_state` intersects to the empty set when a member contributes no platforms, and reports
    `obtainable_all=False`. The mirror `continue`d past such a member, which SKIPPED the constraint."""
    from trophies.models import ConceptBundle

    series = BadgeSeriesFactory(series_slug='bun3')
    st = StageFactory(series_slug='bun3', stage_number=1, title='Gameless member')
    bundle = ConceptBundle.objects.create(stage=st, label='Episodic')
    with_games = ConceptFactory()
    GameFactory(concept=with_games, title_platform=['PS5'], is_obtainable=True)
    bundle.concepts.set([with_games, ConceptFactory()])   # second member has NO games
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    g = _view(series, ProfileFactory())

    assert g.gating_count == 0, 'a bundle with an unreachable member gated the edition'


def test_the_anon_ladder_matches_the_engine_on_bundles_too():
    """The pinning test that used to exist had NO bundles in its catalogue, which is exactly why the
    mirror's bundle drift survived a mutation. There is no mirror any more -- both paths call the engine's
    own predicates over the orchestrator's own units -- and this is what holds that true."""
    series = BadgeSeriesFactory(series_slug='bun4')
    _stage('bun4', 1, title='Plain alive', platforms_obtainable=('PS5',))
    _bundle_stage('bun4', 2, [('PS3', True, False), ('PS5', True, False)], title='Split era')
    _bundle_stage('bun4', 3, [('PS5', True, True), ('PS5', True, False)], title='One delisted')
    _bundle_stage('bun4', 4, [('PS5', True, False), ('PS5', True, False)], title='Both alive')
    GroupBadgeFactory(series=series, platform_group=_ultra(), is_live=True)

    anon = _view(series, None)
    signed_in = _view(series, ProfileFactory())

    assert [s['stage'].stage_number for s in anon.stages] == \
           [s['stage'].stage_number for s in signed_in.stages]
    assert [s['stage'].stage_number for s in anon.retired_stages] == \
           [s['stage'].stage_number for s in signed_in.retired_stages]
    assert [s['stage'].stage_number for s in signed_in.stages] == [1, 4]
    assert [s['stage'].stage_number for s in signed_in.retired_stages] == [3]
