"""The optimizer's dates on the hunter's own clock, highlighted, and the My Challenges feature door (owner,
2026-10-17).

A date is shown in the hunter's SAVED zone when they ever chose one, else the DEVICE's own: a hunter who never
picked a zone is stored as UTC, and must not read UTC times. The server renders the saved-zone text (with its
abbreviation) and marks the `<time>` with the zone the page's script re-renders it in ('' = the device's).
"""
import datetime
import json
from pathlib import Path

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ
from challenges.services import allowance, optimizer_drafts, planner
from challenges.services import challenge_service as svc
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract

pytestmark = pytest.mark.django_db

AZ = CHALLENGE_TYPE_AZ
JS = Path('static/js/challenge-optimizer.js').read_text(encoding='utf-8')
UTILS = Path('static/js/utils.js').read_text(encoding='utf-8')
MY_JS = Path('static/js/my-challenges.js').read_text(encoding='utf-8')
_SEQ = {'n': 0}


def _user(**kw):
    return type('U', (), {'use_24hr_clock': False, 'user_timezone': 'UTC', 'timezone_confirmed_at': None, **kw})()


def _contract(name):
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='dt-%d' % _SEQ['n'], is_live=True, igdb_id=870_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    GameFactory(concept=concept, title_platform=['PS5'])
    return contract


def _hunter(client, *, premium=True, **user_fields):
    user = UserFactory(**user_fields)
    profile = ProfileFactory(user=user, user_is_premium=premium, is_linked=True)
    client.force_login(user)
    with override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False):
        svc.start(profile, AZ)
    return profile


def _options(**kw):
    kw.setdefault('badge_started', False)
    kw.setdefault('types', (AZ,))
    return planner.PlannerOptions(**kw)


def _door(html):
    start = html.rindex('<section', 0, html.index('data-optimizer-door'))
    return html[start:html.index('</section>', start)]


# --- whose clock ------------------------------------------------------------------------------------------

def test_a_hunter_who_never_chose_a_zone_gets_the_devices_clock():
    assert allowance.display_zone(_user()) == ''


def test_a_chosen_zone_is_used_even_when_it_is_utc():
    assert allowance.display_zone(_user(timezone_confirmed_at=timezone.now())) == 'UTC'


def test_a_zone_other_than_the_untouched_default_is_used():
    assert allowance.display_zone(_user(user_timezone='America/New_York')) == 'America/New_York'


def test_the_servers_text_names_its_zone():
    when = datetime.datetime(2026, 10, 24, 0, 12, tzinfo=datetime.timezone.utc)
    with timezone.override('America/New_York'):
        assert allowance.when_label(when, _user()) == 'Fri, Oct 23 at 8:12 PM EDT'
    with timezone.override('UTC'):
        assert allowance.when_label(when, _user(use_24hr_clock=True)) == 'Sat, Oct 24 at 00:12 UTC'


def test_a_date_carries_its_moment_zone_and_clock_for_the_page():
    when = timezone.now()
    ctx = allowance.when_context(when, _user(user_timezone='Asia/Tokyo', use_24hr_clock=True))
    assert ctx['iso'] == when.isoformat() and ctx['tz'] == 'Asia/Tokyo' and ctx['clock'] == '24' and ctx['label']


# --- the optimizer page and its replies ------------------------------------------------------------------

def test_the_banners_next_date_is_highlighted_and_marked_for_the_device_clock(client):
    profile = _hunter(client, premium=False)
    allowance.spend(profile, _options(platforms=('PS5',)), {AZ})

    html = client.get(reverse('challenge_optimizer')).content.decode()

    banner = html[html.index('data-cplan-allowance-text'):]
    banner = banner[:banner.index('</p>')]
    assert '<time class="pp-when" datetime="' in banner and 'data-pp-when data-tz="" data-clock="12"' in banner
    assert 'data-when-tz=""' in html   # the root tells the script whose clock its own dates use


def test_a_saved_zone_marks_every_date_with_it(client):
    profile = _hunter(client, premium=False, user_timezone='Europe/London')
    allowance.spend(profile, _options(platforms=('PS5',)), {AZ})
    html = client.get(reverse('challenge_optimizer')).content.decode()
    assert 'data-tz="Europe/London"' in html and 'data-when-tz="Europe/London"' in html


def test_the_spend_dialog_and_the_resume_card_highlight_their_dates(client):
    profile = _hunter(client, premium=False)
    optimizer_drafts.save(profile, _options(), {'pins': [], 'square_pins': [], 'excluded': [], 'penalized': []})
    html = client.get(reverse('challenge_optimizer')).content.decode()
    until = html[html.index('data-cplan-spend-until>'):]
    assert until[:until.index('</span>')].count('class="pp-when"') == 1

    member = _hunter(client)
    optimizer_drafts.save(member, _options(), {'pins': [], 'square_pins': [], 'excluded': [], 'penalized': []})
    html = client.get(reverse('challenge_optimizer')).content.decode()
    resume = html[html.index('data-cplan-resume-text'):]
    assert 'Saved <time class="pp-when"' in resume[:resume.index('</p>')]


def test_every_reply_with_a_next_free_date_carries_its_moment(client):
    profile = _hunter(client, premium=False)
    _contract('Astro Bot')
    post = lambda **body: client.post(reverse('challenge_optimizer_plan'), content_type='application/json',  # noqa: E731
                                      data=json.dumps({'types': [AZ], 'platforms': ['PS5'], 'badge_started': False, **body}))

    asked = json.loads(post().content)
    spent = json.loads(post(use_free_plan=True).content)
    locked = json.loads(post(platforms=['PS4']).content)

    for reply in (asked, spent['allowance'], locked):
        assert reply['next_free_label'] and datetime.datetime.fromisoformat(reply['next_free_at'])
    assert profile.optimizer_allowance.used_at is not None


# --- the My Challenges door -------------------------------------------------------------------------------

def test_the_door_is_a_feature_card_with_the_star_button(client):
    _hunter(client)
    door = _door(client.get(reverse('my_challenges')).content.decode())
    # The feature treatment (a primary-tinted border), never the page header's left edge: it must not read as a
    # second header right under the real one.
    assert 'border-primary/30' in door and 'border-l-primary' not in door
    assert 'pp-cta pp-cta--rise pp-optdoor__go' in door
    assert 'Optimize my challenges' in door and door.count('text-success') == 3


def test_a_member_with_a_saved_plan_is_told_when(client):
    profile = _hunter(client)
    optimizer_drafts.save(profile, _options(), {'pins': [], 'square_pins': [], 'excluded': [], 'penalized': []})
    door = _door(client.get(reverse('my_challenges')).content.decode())
    assert 'your last plan was saved <time class="pp-when"' in door
    # The button stays "Optimize": "Open my plan" is the optimizer page's own button, one click later.
    assert 'Optimize my challenges' in door and 'Open my plan' not in door


def test_a_member_with_nothing_saved_gets_the_pitch_alone(client):
    _hunter(client)
    door = _door(client.get(reverse('my_challenges')).content.decode())
    assert 'pp-optdoor__status' not in door


def test_a_free_hunter_hears_their_week_is_ready(client):
    _hunter(client, premium=False)
    door = _door(client.get(reverse('my_challenges')).content.decode())
    assert 'Your free optimized plan for this week is ready.' in door


def test_a_free_hunter_mid_week_sees_when_the_next_plan_comes(client):
    profile = _hunter(client, premium=False)
    allowance.spend(profile, _options(), {AZ})
    door = _door(client.get(reverse('my_challenges')).content.decode())
    assert 'Your next free plan is ready <time class="pp-when"' in door


def test_the_new_chip_retires_itself(client, monkeypatch):
    from challenges import views
    _hunter(client)
    monkeypatch.setattr(views, 'OPTIMIZER_NEW_UNTIL', timezone.localdate() + datetime.timedelta(days=1))
    assert '>New</span>' in _door(client.get(reverse('my_challenges')).content.decode())
    monkeypatch.setattr(views, 'OPTIMIZER_NEW_UNTIL', timezone.localdate())
    assert '>New</span>' not in _door(client.get(reverse('my_challenges')).content.decode())


def test_the_door_costs_a_free_hunter_two_small_reads_and_nobody_any_without_a_run(client, django_assert_num_queries):
    from challenges.services.optimizer_drafts import door_status
    profile = ProfileFactory(user_is_premium=False)
    with django_assert_num_queries(2):
        door_status(profile, profile.user)
    user = UserFactory()
    ProfileFactory(user=user, user_is_premium=True, is_linked=True)
    client.force_login(user)
    assert 'optimizer' not in client.get(reverse('my_challenges')).context   # no run: no door, no reads


# --- the scripts ------------------------------------------------------------------------------------------

def test_both_pages_put_their_dates_on_the_hunters_clock_guardedly():
    for source in (JS, MY_JS):
        assert 'if (PP.TimeFormatter && PP.TimeFormatter.localizeWhen) { PP.TimeFormatter.localizeWhen(document); }' in source


def test_the_shared_formatter_falls_back_to_the_device_and_the_servers_text():
    when = UTILS[UTILS.index('    when(timestamp,'):UTILS.index('    localizeWhen(root')]
    assert "hourCycle: hour24 ? 'h23' : 'h12', timeZoneName: 'short'" in when
    # A zone the browser does not know keeps the server's text, never the device's zone in its place.
    assert 'return render(timeZone);' in when and 'render(null)' not in when
    assert "} catch (e) {\n            return '';" in when
    loc = UTILS[UTILS.index('    localizeWhen(root'):]
    assert "timeZone: el.dataset.tz || null, hour24: el.dataset.clock === '24'" in loc[:600]
    assert 'if (text) el.textContent = text;' in loc[:600]


def test_dates_the_optimizer_writes_itself_are_highlighted_too():
    show = JS[JS.index('function showInUse('):JS.index('function applyAnswers(')]
    assert 'text.appendChild(whenNode(allowanceNext));' in show
    spend = JS[JS.index('function openSpend('):JS.index('if (spendDialog) {')]
    assert "until.textContent = ''; until.appendChild(whenNode(next));" in spend
    assert "el.className = 'pp-when';" in JS[JS.index('function whenNode('):JS.index('function nextFrom(')]


def test_a_locked_reply_is_said_on_the_pages_clock_not_the_servers():
    handle = JS[JS.index('if (reply && reply.plan_locked) {'):]
    handle = handle[:handle.index('};')]
    said = "say(reply.next_free_label ? lockedLine() : (reply.error || fallback), true);"
    assert said in handle and handle.index('showInUse(') < handle.index(said)


def test_a_repeat_spend_reports_the_weeks_real_next_date(client, monkeypatch):
    # The race: this tab was told to spend, but another tab spent the week on THESE answers (two days ago, say)
    # just before this one's spend ran, so the spend is a no-op and the week's date is the earlier one.
    profile = _hunter(client, premium=False)
    _contract('Astro Bot')
    earlier = timezone.now() - datetime.timedelta(days=2)
    real_spend = allowance.spend

    def other_tab_first(prof, options, active_types, now=None):
        real_spend(prof, options, active_types, now=earlier)
        return real_spend(prof, options, active_types, now=now)
    monkeypatch.setattr(allowance, 'spend', other_tab_first)

    resp = client.post(reverse('challenge_optimizer_plan'), content_type='application/json',
                       data=json.dumps({'types': [AZ], 'platforms': ['PS5'], 'badge_started': False,
                                        'use_free_plan': True}))

    at = datetime.datetime.fromisoformat(json.loads(resp.content)['allowance']['next_free_at'])
    assert abs((at - (earlier + allowance.FREE_PLAN_WINDOW)).total_seconds()) < 1
