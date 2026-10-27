"""The Identity and social review's fixes (docs/design/reviews/identity.md, ID-02 to ID-16, 2026-10-26).

ID-01 (the reveal engine's batch) has its own real-browser file, test_stagger_reveal_batch_browser.py; ID-11's
ratings tests live in test_profile_ratings.py and the Hunters sort in test_profiles_browse.py, beside the
tests they replaced.
"""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import BadgeSeriesFactory, ConceptFactory, GameFactory, ProfileFactory, ProfileGameFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def _profile(profile, tab=None):
    return f'/hunters/{profile.psn_username}/' + (f'?tab={tab}' if tab else '')


def _header(body):
    start = body.index('pp-phero__id')
    return body[start:body.index('</section>', start)]


# -- ID-02: a private history shows no figures ---------------------------------------------------------------

def test_a_private_profile_says_so_instead_of_showing_zeros(client):
    hunter = ProfileFactory(is_linked=True, psn_history_public=False, trophy_level=12, progress=63, total_plats=0)
    header = _header(client.get(_profile(hunter), **CF).content.decode())
    assert "This hunter's trophy history is private on PlayStation." in header
    for absent in ('pp-phero__tiers', 'pp-phero__facts', 'pp-phero__lvl', 'data-sync-provisional'):
        assert absent not in header, f'{absent} still renders figures PSN no longer sends'
    assert '--lvl: 0;' in header, 'the ring still draws the stale progress arc'


def test_your_own_private_profile_tells_you_what_to_do(client):
    me = ProfileFactory(is_linked=True, psn_history_public=False)
    client.force_login(me.user)
    header = _header(client.get(_profile(me), **CF).content.decode())
    assert 'Your trophy history is private on PlayStation' in header
    assert 'Set it to public in your PlayStation privacy settings, then refresh.' in header


# -- ID-03: one line of facts, a figure that would read 0 left out --------------------------------------------

def test_the_header_is_one_line_of_facts_not_four_tiles(client):
    hunter = ProfileFactory(is_linked=True, total_trophies=40, total_bronzes=40)
    header = _header(client.get(_profile(hunter), **CF).content.decode())
    assert 'scard' not in header, 'the stat tiles are back'
    facts = header[header.index('pp-phero__facts'):]
    facts = facts[:facts.index('</p>')]
    assert ' trophies</span>' in facts
    assert 'completed' not in facts and 'average completion' not in facts, 'a zero is shown as a fact'


def test_seven_tabs_fit_two_rows_on_a_phone():
    page = _read('templates/trophies/profile_detail.html')
    assert 'class="pp-switch pp-switch--many" role="tablist" aria-label="Profile sections"' in page
    css = _read('static/css/components/switcher.css')
    block = css[css.index('@media (max-width: 639px)'):]
    block = block[:block.index('\n}\n')]
    assert '.pp-switch--many .pp-switch__chip svg { display: none; }' in block


# -- ID-04: Leaderboards' phone controls -----------------------------------------------------------------------

def test_the_leaderboard_sub_tabs_never_wrap_and_the_three_ways_in_share_a_row():
    css = _read('static/css/components/leaderboards.css')
    assert re.search(r'\.lb-bar \.pp-switch__chip,\s*\.lb-subbar \.pp-switch__chip \{[^}]*white-space: nowrap', css)
    phone = css[css.index('/* MOBILE FIT. Five stacked rows'):]
    assert '.lb-controls .lb-jumpbar { margin-top: 8px; flex-wrap: nowrap; }' in phone
    assert '.lb-controls .gd-lb__find { flex: 1 1 0; min-width: 0; order: 0; }' in phone


# -- ID-05: Hunters carries no site-wide tiles ----------------------------------------------------------------

def test_hunters_carries_no_site_wide_tiles(client):
    ProfileFactory(is_linked=True)
    resp = client.get(reverse('profiles_list'))
    body = resp.content.decode()
    assert 'hunters_stats' not in resp.context
    for label in ('Hunters tracked', 'Trophies tracked', 'Earned in last 24h'):
        assert label not in body


# -- ID-06: the recap's controls keep clear of the beat --------------------------------------------------------

def test_the_recap_keeps_a_foot_strip_for_its_controls():
    css = _read('static/css/components/recap-stage.css')
    assert 'position: absolute; inset: 0.5rem 1rem 4.25rem;' in css
    assert '.rcx .recap-slide { inset: 1rem 1.5rem 4.25rem; }' in css
    assert '.rcx__aim--hold { left: calc(50% - 1.25rem); top: auto; bottom: 1.5rem;' in css
    assert re.search(r'@media \(max-width: 767px\) \{\s*\.rcx__aim--prev, \.rcx__aim--next \{ top: auto; bottom: 1\.5rem;', css)
    assert '@media (min-width: 768px) and (max-height: 720px) { .rcx .rcp-cal__cell { width: 2rem; height: 2rem; } }' in css


def test_a_slide_is_measured_for_overflow_as_it_arrives():
    """The after-render check ran while the stage was hidden, so every slide measured 0 and none was marked."""
    js = _read('static/js/monthly-recap.js')
    go = js[js.index('    goToSlide(index) {'):]
    go = go[:go.index('\n    }\n')]
    assert go.index('this.fitSlide(slideEls[index]);') < go.index('this.armBeat(index, slideEls[index]);')
    assert "el.classList.toggle('has-overflow', el.scrollHeight > el.clientHeight);" in js


# -- ID-07: "Level" says which ---------------------------------------------------------------------------------

def test_each_level_says_which_level_it_is(client):
    hunter = ProfileFactory(is_linked=True, trophy_level=386)
    page = client.get(_profile(hunter), **CF).content.decode()
    assert '<span class="pp-phero__lvl-k" aria-hidden="true">Trophy Lv</span>' in page
    assert 'aria-label="Trophy level 386' in page
    assert '<span class="pp-hcard__lbl">Trophy Lv</span>' in client.get(reverse('profiles_list')).content.decode()
    assert '<span class="lab-dna__cap">Pursuer Lv</span>' in _read('templates/partials/components/_disciplines_ring.html')
    assert 'line-height: 1.2;">Pursuer Lv</span>' in _read('templates/shareables/profile_card.html')
    from trophies.views.badge_views import OverallBadgeLeaderboardsView
    assert OverallBadgeLeaderboardsView.FIGURES['career'] == ('XP', 'pursuer lv')


# -- ID-08: a badge says what kind it is ----------------------------------------------------------------------

def test_a_badge_card_names_its_kind():
    from tests.factories import GroupBadgeFactory
    from trophies.services.collection_service import badge_frame

    gb = GroupBadgeFactory(series=BadgeSeriesFactory(badge_type='franchise'), is_live=True)
    assert badge_frame(gb, {gb.id: False}, {}, 1)['badge_kind'] == 'Franchise'
    for rel in ('templates/components/collection_gallery.html',
                'templates/trophies/partials/profile_detail/tabs/badges_tab.html'):
        card = _read(rel)
        kind = card.index('<span class="pp-gallery__kind">{{ badge.badge_kind }}</span>')
        assert kind < card.index('<span class="pp-gallery__name">{{ badge.series_name }}</span>'), rel


def test_the_badge_page_eyebrow_leads_with_the_kind(client):
    from tests.engine.test_badge_detail import _group, _stage

    series = BadgeSeriesFactory(series_slug='nfs-fr', name='Need for Speed', badge_type='franchise')
    _stage(series, 1, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    body = client.get(reverse('badge_detail', kwargs={'series_slug': 'nfs-fr'})).content.decode()
    assert re.search(r'>\s*Franchise badge(?: &middot; [^<]+)?</p>', body)


# -- ID-09: numbers that say what they count -----------------------------------------------------------------

def test_the_clean_board_says_its_trophies_are_clean():
    from trophies.views.badge_views import OverallBadgeLeaderboardsView as V
    assert V.FIGURES['clean'] == ('platinums', 'clean trophies')
    assert 'Clean trophies, also outside shovelware, settle a tie.' in V.MEANINGS['clean']


def test_plat_cards_says_what_its_middle_figure_counts():
    page = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', _read('templates/shareables/plat_cards.html'), flags=re.S)
    assert 'without a platinum</span>' in page and '100% clears' not in page
    assert "document.querySelectorAll('.pp-factline [data-countup]')" in _read('static/js/plat-cards.js')


# -- ID-10: Plat Cards has no tiles ---------------------------------------------------------------------------

def test_plat_cards_opens_on_a_line_of_facts(client):
    me = ProfileFactory(is_linked=True)
    client.force_login(me.user)
    body = client.get(reverse('my_shareables')).content.decode()
    head = body[body.index('<h1'):body.index('</section>')]
    assert 'scard' not in head and 'pp-factline' in head


# -- ID-12: a profile's lists carry your progress, not the owner's name ---------------------------------------

def test_a_profiles_lists_drop_the_byline_and_carry_your_progress(client):
    from gamelists.services import game_list_service as svc

    owner = ProfileFactory(is_linked=True, psn_username='curator')
    game_list = svc.create_list(owner, name='Shelf', is_public=True)
    concepts = [ConceptFactory() for _ in range(2)]
    for concept in concepts:
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)
    reader = ProfileFactory(is_linked=True)
    ProfileGameFactory(profile=reader, game=concepts[0].games.first(), progress=100, has_plat=True)
    client.force_login(reader.user)

    body = client.get(_profile(owner, 'lists'), **CF).content.decode()
    grid = body[body.index('id="lists-grid"'):]
    assert 'gl-card__author' not in grid, 'the byline repeats the profile it is on'
    assert '<b class="gl-card__done">1</b> of 2 platinumed' in grid


# -- ID-13: your own profile is My Pursuit's ------------------------------------------------------------------

def test_your_own_profile_sits_under_my_pursuit(client):
    me = ProfileFactory(is_linked=True, psn_username='me_myself')
    other = ProfileFactory(is_linked=True, psn_username='someone')
    client.force_login(me.user)

    mine = client.get(_profile(me), **CF)
    assert [c['text'] for c in mine.context['breadcrumb']] == ['Home', me.display_psn_username or me.psn_username]
    assert mine.context['hub_section'] == 'my_pursuit'
    assert mine.context['hub_subnav_active_slug'] is None

    theirs = client.get(_profile(other), **CF)
    assert [c['text'] for c in theirs.context['breadcrumb']][:2] == ['Home', 'Hunters']
    assert theirs.context['hub_section'] == 'community'

    from tests.engine.test_activity_service import _at, _earn
    _earn(me, GameFactory(), _at(1))
    own_day = client.get(reverse('profile_day', kwargs={'psn_username': me.psn_username,
                                                        'day': _at(1).date().isoformat()}), **CF)
    assert own_day.context['hub_section'] == 'my_pursuit'
    assert 'Hunters' not in [c['text'] for c in own_day.context['breadcrumb']]


# -- ID-14: rows link to the canonical name ------------------------------------------------------------------

def test_a_leaderboard_row_links_without_a_redirect(client):
    from tests.engine.test_leaderboards_landing import _ranked

    hunter = _ranked('MixedCase', plats=3, trophies=30)
    body = client.get(reverse('overall_badge_leaderboards')).content.decode()
    assert f'href="/hunters/{hunter.psn_username}/"' in body
    assert 'href="/hunters/MixedCase/"' not in body
    assert hunter.psn_username == hunter.psn_username.lower()


# -- ID-15: a rewatch is free, and NEW means recent ----------------------------------------------------------

def test_a_rewatch_lifts_the_quiz_gate():
    page = _read('templates/recap/monthly_recap.html')
    assert '{% if not first_visit %} data-rewatch{% endif %}' in page
    js = _read('static/js/monthly-recap.js')
    for nav in ('    nextSlide() {', '    prevSlide() {'):
        body = js[js.index(nav):]
        assert 'if (this.quizHolds()) return;' in body[:body.index('\n    }\n')], nav
    # ONE answer for the nav, the edge buttons and the beat clock (the audit found the last two still gated).
    gates = js[js.index('    quizGates(type) {'):]
    gates = gates[:gates.index('\n    }\n')]
    assert gates.index('if (this.isRewatch) return false;') < gates.index('canNavigate')
    for site in ('    syncBeatState(type) {', '    updateNavigationButtons() {', '    quizHolds() {'):
        body = js[js.index(site):]
        assert 'this.quizGates(' in body[:body.index('\n    }\n')], site
    assert js.count('!this.quizManager.canNavigate(') == 1, 'a quiz gate is asked outside quizGates again'


def test_a_rewatch_score_counts_the_decks_quizzes_not_the_answered_ones():
    js = _read('static/js/monthly-recap.js')
    fill = js[js.index('    fillQuizScore(slideEl) {'):]
    fill = fill[:fill.index('\n    }\n')]
    assert "set('[data-score-total]', `/ ${quizzes}`);" in fill
    assert 'You skipped ${skipped} this time.' in fill


def test_only_a_recently_finished_unwatched_month_is_new():
    from tests.engine.test_recap_archive_page import _hunter, _trophy_at, _utc
    from trophies.models import MonthlyRecap
    from trophies.services.monthly_recap_service import MonthlyRecapService

    hunter = _hunter()
    now = timezone.now()
    months = []
    for back in (1, 2, 3, 6):
        idx = now.year * 12 + (now.month - 1) - back
        months.append((idx // 12, idx % 12 + 1))
    for year, month in months:
        _trophy_at(hunter, _utc(year, month, 15))
    MonthlyRecap.objects.create(profile=hunter, year=months[0][0], month=months[0][1],
                                total_trophies_earned=1, platinums_earned=0, is_finalized=True,
                                has_been_viewed=True)

    archive = MonthlyRecapService.get_archive(hunter)
    flags = {(m['year'], m['month']): m['is_new'] for y in archive['years'] for m in y['months']}
    assert flags[months[0]] is False                # last month, but watched
    assert flags[months[1]] is True                 # two months back, unwatched: still inside the window
    assert flags[months[2]] is False and flags[months[3]] is False, 'an old unwatched month still reads NEW'


# -- ID-16: a day page reaches its neighbours ----------------------------------------------------------------

def test_a_day_page_links_to_the_days_either_side(client):
    from tests.engine.test_activity_service import _at, _earn

    hunter = ProfileFactory(is_linked=True)
    game = GameFactory()
    for days_ago in (9, 5, 1):
        _earn(hunter, game, _at(days_ago))
    day = _at(5).date()

    body = client.get(reverse('profile_day', kwargs={'psn_username': hunter.psn_username,
                                                     'day': day.isoformat()}), **CF).content.decode()
    nav = body[body.index('pp-actday__nav'):]
    nav = nav[:nav.index('</nav>')]
    assert f'/day/{_at(9).date().isoformat()}/' in nav and 'rel="prev"' in nav
    assert f'/day/{_at(1).date().isoformat()}/' in nav and 'rel="next"' in nav

    last = client.get(reverse('profile_day', kwargs={'psn_username': hunter.psn_username,
                                                     'day': _at(1).date().isoformat()}), **CF).content.decode()
    assert 'rel="next"' not in last, 'the newest day offers a day that does not exist'


def test_the_neighbours_are_two_one_row_lookups(django_assert_num_queries):
    from tests.engine.test_activity_service import _at, _earn
    from trophies.services.activity_service import adjacent_days

    hunter = ProfileFactory(is_linked=True)
    game = GameFactory()
    for days_ago in range(1, 40):
        _earn(hunter, game, _at(days_ago))
    with django_assert_num_queries(2):
        before, after = adjacent_days(hunter, _at(20).date())
    assert (before, after) == (_at(21).date(), _at(19).date())
    assert adjacent_days(hunter, (timezone.now() + timedelta(days=5)).date())[1] is None


# -- Average completion is per game (owner, 2026-10-26) ------------------------------------------------------

def _library(profile, *games):
    """(earned, unearned, progress) per game."""
    for earned, unearned, progress in games:
        ProfileGameFactory(profile=profile, game=GameFactory(), earned_trophies_count=earned,
                           unearned_trophies_count=unearned, progress=progress)


def test_average_completion_is_the_mean_of_each_games_percent():
    """Not earned / total: one 300-trophy list would sink a library of finished games. PSN's own per-game
    figure, averaged, the number PSN and PSNProfiles show."""
    from trophies.services.profile_stats_service import update_profile_trophy_counts

    hunter = ProfileFactory(is_linked=True)
    _library(hunter, (10, 290, 3), (12, 0, 100))
    update_profile_trophy_counts(hunter)
    hunter.refresh_from_db()
    assert round(hunter.avg_progress, 1) == 51.5, 'the library-wide share (7.3%) is back'
    assert (hunter.total_trophies, hunter.total_unearned) == (22, 290)


def test_average_completion_keeps_the_hunters_hide_settings():
    from trophies.services.profile_stats_service import update_profile_trophy_counts

    hunter = ProfileFactory(is_linked=True, hide_zeros=True)
    _library(hunter, (0, 50, 0), (12, 0, 100), (5, 5, 50))
    update_profile_trophy_counts(hunter)
    hunter.refresh_from_db()
    assert hunter.avg_progress == 75.0, 'a game with nothing earned still counts under hide_zeros'


def test_the_header_says_how_many_of_how_many_trophies(client):
    hunter = ProfileFactory(is_linked=True, total_trophies=4017, total_unearned=2925, total_bronzes=4017)
    header = _header(client.get(_profile(hunter), **CF).content.decode())
    facts = header[header.index('pp-phero__facts'):]
    assert re.search(r'>4,017</b><span data-live-of="6942"> of 6,942</span> trophies</span>', facts)

    done = ProfileFactory(is_linked=True, total_trophies=40, total_unearned=0, total_bronzes=40)
    facts = _header(client.get(_profile(done), **CF).content.decode())
    assert re.search(r'>40</b> trophies</span>', facts), 'a hunter with nothing unearned reads "40 of 40"'


def test_mid_sync_the_of_total_hides_once_the_live_total_passes_it(client):
    """The total is stored earned + unearned, which waits for finalize; a sync bringing new games carries the
    live (tier-summed) total past it. Hidden at render, and by refresh-control.js as the total ticks."""
    hunter = ProfileFactory(is_linked=True, sync_status='syncing', total_trophies=100, total_unearned=10,
                            total_bronzes=150)
    header = _header(client.get(_profile(hunter), **CF).content.decode())
    assert '<span data-live-of="110" hidden> of 110</span>' in header
    js = _read('static/js/refresh-control.js')
    tally = js[js.index('    function applyTally(stats) {'):]
    tally = tally[:tally.index('\n    }\n')]
    assert 'if (ofEl) { ofEl.hidden = total > Number(ofEl.dataset.liveOf); }' in tally


def test_the_lobby_shows_one_completion_percentage():
    """"Earn rate" was earned / total trophies: beside the per-game average completion it was a second,
    different percentage (owner: a count, not two percentages)."""
    home = re.sub(r'\{#.*?#\}', '', _read('templates/trophies/home.html'))
    assert 'Earn rate' not in home and 'snapshot.earn_rate' not in home
