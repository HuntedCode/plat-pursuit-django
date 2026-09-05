"""The Admin Hub: the gate, the landing, and the way in.

The gate is the part worth being paranoid about, and it is a DIFFERENT gate from the Mod Center's.
`/mod/` admits moderators and admins; `/staff/` admits admins only. That distinction rests entirely
on `CustomUser.save()` keeping `is_staff` false for a moderator, so the assertion that matters here
is the one about moderators -- who are trusted people, and are exactly who would find these URLs.
"""
import pathlib

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import AdminAction
from tests.factories import (ConceptFactory, GameFactory, ProfileFactory, UserFactory)
from trophies.models import BlurbReport, GameFlag, ModerationAction, UserConceptRating

pytestmark = pytest.mark.django_db

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _user(role=''):
    user = UserFactory()
    if role:
        user.role = role
        user.save()
    return user


def _every_staff_url():
    """Every route under `/staff/`, read off the URL conf.

    Enumerated rather than listed, for the reason the Mod Center's twin gives: a hand-written list is
    what somebody forgets. Redirect stubs are excluded -- the parked notification routes under
    `/staff/notifications/` are 302s to the homepage by design and answer everyone the same way.
    """
    from django.views.generic import RedirectView

    from plat_pursuit import urls as root_urls

    found = []
    for pattern in root_urls.urlpatterns:
        route = getattr(getattr(pattern, 'pattern', None), '_route', '')
        if not route.startswith('staff/'):
            continue
        view_class = getattr(pattern.callback, 'view_class', None)
        if view_class is not None and issubclass(view_class, RedirectView):
            continue
        found.append('/' + route.replace('<int:pk>', '1'))
    return found


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────

def test_the_url_conf_still_has_the_staff_urls_this_file_thinks_it_does():
    """A guard on the guard: if the routes move or gain a stub, the sweep below could quietly
    exercise nothing and stay green."""
    urls = _every_staff_url()

    assert '/staff/' in urls, 'the hub itself is not being swept'
    assert len(urls) >= 5, f'expected the hub plus the four kept tools, found {urls}'


def test_a_signed_out_visitor_is_sent_to_login(client):
    resp = client.get(reverse('admin_hub'))

    assert resp.status_code == 302 and '/login' in resp.url.lower()


def test_an_ordinary_hunter_is_sent_home_not_told_it_exists(client):
    client.force_login(_user())

    resp = client.get(reverse('admin_hub'))

    assert resp.status_code == 302 and resp.url == '/'


def test_a_moderator_cannot_reach_anything_under_staff(client):
    """THE assertion this hub rests on, and the one that exists nowhere else.

    Moderators are trusted, which is exactly why this needs a test rather than an assumption: they
    are the people most likely to find these URLs, and `is_staff` is the only thing keeping them out.
    """
    moderator = _user('moderator')
    assert moderator.is_staff is False, 'the role lockstep changed; /staff/ is now open to mods'
    client.force_login(moderator)

    for url in _every_staff_url():
        resp = client.get(url)
        assert resp.status_code == 302, f'{url} answered a moderator with {resp.status_code}'
        assert resp.url == '/', f'{url} told a moderator it exists'


def test_an_admin_gets_in(client):
    client.force_login(_user('admin'))

    assert client.get(reverse('admin_hub')).status_code == 200


def test_a_superuser_with_no_role_gets_in(client):
    """Superusers have no `role` at all. They must not be locked out of the tools they are most
    likely to be asked to fix."""
    user = UserFactory()
    user.is_superuser = user.is_staff = True
    user.save()
    client.force_login(user)

    assert client.get(reverse('admin_hub')).status_code == 200


def test_a_deactivated_admin_is_locked_out(client):
    admin = _user('admin')
    client.force_login(admin)
    admin.is_active = False
    admin.save()

    assert client.get(reverse('admin_hub')).status_code == 302


# ── the landing ──────────────────────────────────────────────────────────────────────────────────

def _report():
    concept = ConceptFactory(unified_title='Hollow Knight')
    GameFactory(concept=concept)
    rating = UserConceptRating.objects.create(
        profile=ProfileFactory(is_linked=True), concept=concept, concept_trophy_group=None,
        blurb='some words', difficulty=5, grindiness=5, hours_to_platinum=20,
        fun_ranking=8, overall_rating=4.0,
    )
    return BlurbReport.objects.create(
        rating=rating, reporter=ProfileFactory(is_linked=True), reason='harassment')


def test_the_landing_counts_the_same_reports_the_mod_center_does(client):
    """One definition. A hub claiming a different amount of work from the page it points at is worse
    than a hub with no number -- the lesson the Mod Center's own marker paid for."""
    _report()
    GameFlag.objects.create(game=GameFactory(), reporter=ProfileFactory(is_linked=True),
                            flag_type='delisted')
    client.force_login(_user('admin'))

    hub = client.get(reverse('admin_hub'))
    mod_center = client.get(reverse('mod_center'))

    assert hub.context['reports_waiting'] == mod_center.context['open_total'] == 2


def test_the_landing_shows_entries_from_both_logs(client):
    """The whole point of one rail: "what has been happening here" is a single question, and an admin
    who has to check two lists will check one."""
    ModerationAction.objects.create(action='blurb_hidden', reason='a slur',
                                    actor_label='Mod', target_label='a quick take')
    AdminAction.objects.create(action='restriction_applied', reason='spam, third time',
                               actor_label='Admin', target_label='someone')
    client.force_login(_user('admin'))

    body = client.get(reverse('admin_hub')).content.decode()

    assert 'a slur' in body and 'spam, third time' in body
    assert 'Moderation' in body and 'Admin' in body


def test_the_rail_is_newest_first_across_both_logs(client):
    """Interleaved by time, not one log after the other -- otherwise the rail is two lists stacked."""
    from core.staff_views import recent_activity

    first = ModerationAction.objects.create(action='blurb_hidden', reason='oldest',
                                            target_label='x')
    second = AdminAction.objects.create(action='restriction_applied', reason='middle',
                                        target_label='y')
    third = ModerationAction.objects.create(action='blurb_hidden', reason='newest',
                                            target_label='z')

    rail = recent_activity()

    assert [row['entry'].reason for row in rail] == ['newest', 'middle', 'oldest']
    assert [row['source'] for row in rail] == ['Moderation', 'Admin', 'Moderation']
    assert {row['entry'].pk for row in rail} == {first.pk, second.pk, third.pk}


def test_the_rail_is_bounded(client):
    """Both logs grow forever. A landing that renders all of them is a landing that stops loading."""
    from core.staff_views import RECENT_LIMIT, recent_activity

    for n in range(RECENT_LIMIT + 5):
        ModerationAction.objects.create(action='blurb_hidden', reason=f'{n}', target_label='x')
        AdminAction.objects.create(action='restriction_applied', reason=f'{n}', target_label='y')

    assert len(recent_activity()) == RECENT_LIMIT


def test_the_landing_does_not_query_per_entry(client):
    """The rail badges reversed entries, and `is_reversed` is a query per row without the prefetch."""
    client.force_login(_user('admin'))
    ModerationAction.objects.create(action='blurb_hidden', reason='one', target_label='x')

    with CaptureQueriesContext(connection) as few:
        client.get(reverse('admin_hub'))

    for n in range(8):
        ModerationAction.objects.create(action='blurb_hidden', reason=f'{n}', target_label='x')
        AdminAction.objects.create(action='restriction_applied', reason=f'{n}', target_label='y')

    with CaptureQueriesContext(connection) as many:
        client.get(reverse('admin_hub'))

    assert len(many.captured_queries) <= len(few.captured_queries) + 2, (
        f'{len(few.captured_queries)} queries for 1 entry, {len(many.captured_queries)} for 17')


def test_the_landing_survives_redis_being_unreachable(client, monkeypatch):
    """Every number but one is a DB aggregate; the worker backlog is not. An admin's route to every
    other tool must not depend on the thing they came to check on."""
    def _boom(*args, **kwargs):
        raise ConnectionError('redis is down')

    monkeypatch.setattr('core.staff_views.redis_client.llen', _boom)
    client.force_login(_user('admin'))

    resp = client.get(reverse('admin_hub'))

    assert resp.status_code == 200
    assert resp.context['worker_backlog'] is None
    assert 'cannot reach Redis' in resp.content.decode(), (
        'an unreachable Redis rendered as a number, which reads as "the workers are idle"')


def test_the_worker_card_names_the_deepest_queue(client, monkeypatch):
    """The happy branch, pinned deterministically. Redis is reachable in this environment, so the
    un-patched test above would exercise it -- with whatever happens to be in the dev queues, which
    is not an assertion. The number that matters is WHICH queue is deepest: a total alone tells an
    admin there is a backlog but not where.
    """
    depths = {'orchestrator_jobs': 2, 'high_priority_jobs': 0, 'medium_priority_jobs': 41,
              'low_priority_jobs': 7, 'bulk_priority_jobs': 0}
    monkeypatch.setattr('core.staff_views.redis_client.llen', lambda name: depths[name])
    client.force_login(_user('admin'))

    resp = client.get(reverse('admin_hub'))

    assert resp.context['worker_backlog'] == {
        'queue': 'medium_priority_jobs', 'depth': 41, 'total': 50}
    assert 'medium_priority_jobs' in resp.content.decode()


def test_the_worker_card_watches_every_queue(client, monkeypatch):
    """It reads the queue list from the monitoring view rather than restating it. A hub quietly
    watching four of five would report all clear while the fifth was drowning."""
    from trophies.views.admin_views import WORKER_QUEUES

    seen = []
    monkeypatch.setattr('core.staff_views.redis_client.llen',
                        lambda name: seen.append(name) or 0)
    client.force_login(_user('admin'))

    client.get(reverse('admin_hub'))

    assert seen == WORKER_QUEUES


def test_the_hub_offers_no_back_link_to_itself(client):
    client.force_login(_user('admin'))

    body = client.get(reverse('admin_hub')).content.decode()

    assert body.count(f'href="{reverse("admin_hub")}"') == 0


# ── the way in ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('page', ['mod_center', 'mod_quick_takes', 'mod_game_flags'])
def test_an_admin_reaches_the_hub_from_the_mod_center(client, page):
    """The hub is bookmark-reached by design, with one exception: it sits a click from the surface
    every admin already visits."""
    client.force_login(_user('admin'))

    body = client.get(reverse(page)).content.decode()

    assert f'href="{reverse("admin_hub")}"' in body


@pytest.mark.parametrize('page', ['mod_center', 'mod_quick_takes', 'mod_game_flags'])
def test_a_moderator_is_not_shown_a_door_they_cannot_open(client, page):
    client.force_login(_user('moderator'))

    body = client.get(reverse(page)).content.decode()

    assert reverse('admin_hub') not in body


def test_the_navbar_still_carries_no_admin_link():
    """The Mod Center's doc asked a future admin dashboard to supply its own reason for a navbar row
    rather than inheriting that precedent. It does not have one: nothing in the hub accrues while
    nobody is looking. This pins that answer, so re-opening it has to be deliberate."""
    navbar = (ROOT / 'templates' / 'partials' / 'navbar.html').read_text(encoding='utf-8')

    assert 'admin_hub' not in navbar


# ── the shell ────────────────────────────────────────────────────────────────────────────────────

def test_every_staff_page_on_the_shell_refuses_indexing(client):
    """Of the five staff pages that predate the shell, exactly one carried this. Putting it in the
    shell means a page cannot be added without it."""
    client.force_login(_user('admin'))

    body = client.get(reverse('admin_hub')).content.decode()

    assert 'noindex, nofollow' in body


def test_robots_still_blocks_the_whole_staff_namespace():
    robots = (ROOT / 'static' / 'robots.txt').read_text(encoding='utf-8')

    assert 'Disallow: /staff/' in robots


def test_no_em_dashes_in_what_an_admin_reads(client):
    """House style, asserted against the RENDERED page rather than the source."""
    import html
    import re

    client.force_login(_user('admin'))
    AdminAction.objects.create(action='restriction_applied', reason='spam', target_label='someone')

    body = client.get(reverse('admin_hub')).content.decode()
    for tag in ('script', 'style'):
        body = re.sub(r"(?is)<%s[^>]*>.*?</%s\s*>" % (tag, tag), " ", body)
    text = html.unescape(re.sub(r"(?s)<!--.*?-->", " ", body))

    for offender, label in (('—', 'an em dash'), ('–', 'an en dash'),
                            (' -- ', 'a double hyphen reading as an em dash')):
        at = text.find(offender)
        assert at == -1, f'{label} reached the page: ...{text[max(0, at - 90):at + 90]!r}...'


# ── the decision log ─────────────────────────────────────────────────────────────────────────────
#
# The one power the Mod Center does not have. `reverse_action` has existed since the log was built
# and has been unreachable from any page until now.

def _decision():
    """A real, reversible decision made by a moderator."""
    from trophies.services import moderation_service

    return moderation_service.hide_blurb(_report(), _user('moderator'), 'a slur')


def test_a_moderator_cannot_reach_the_decision_log(client):
    """Deciding is theirs; taking a colleague's decision back is not."""
    client.force_login(_user('moderator'))

    resp = client.get(reverse('admin_decisions'))

    assert resp.status_code == 302 and resp.url == '/'


def test_a_moderator_cannot_reverse_anything(client):
    """Asserted on the DATABASE. A gate that redirects after acting passes a status-code test."""
    decision = _decision()
    client.force_login(_user('moderator'))

    client.post(reverse('admin_reverse_decision', args=[decision.pk]), {'reason': 'let me in'})

    decision.blurb_report.rating.refresh_from_db()
    assert decision.blurb_report.rating.blurb_hidden is True, 'a moderator reversed a decision'
    assert ModerationAction.objects.count() == 1, 'a moderator wrote a reversal'


def test_an_admin_sees_every_decision_with_its_reason(client):
    decision = _decision()
    client.force_login(_user('admin'))

    body = client.get(reverse('admin_decisions')).content.decode()

    assert 'a slur' in body, 'the reason is the part that makes the log worth keeping'
    assert decision.target_label in body


def test_an_admin_can_reverse_a_decision_from_the_page(client):
    decision = _decision()
    client.force_login(_user('admin'))

    client.post(reverse('admin_reverse_decision', args=[decision.pk]),
                {'reason': 'quoting, not endorsing'})

    decision.blurb_report.rating.refresh_from_db()
    assert decision.blurb_report.rating.blurb_hidden is False
    assert ModerationAction.objects.count() == 2, 'the original was edited instead of added to'


def test_reversing_without_a_reason_changes_nothing(client):
    decision = _decision()
    client.force_login(_user('admin'))

    client.post(reverse('admin_reverse_decision', args=[decision.pk]), {'reason': '  '})

    decision.blurb_report.rating.refresh_from_db()
    assert decision.blurb_report.rating.blurb_hidden is True
    assert ModerationAction.objects.count() == 1


def test_a_reversal_cannot_be_performed_by_GET(client):
    decision = _decision()
    client.force_login(_user('admin'))

    resp = client.get(reverse('admin_reverse_decision', args=[decision.pk]))

    assert resp.status_code == 405
    decision.blurb_report.rating.refresh_from_db()
    assert decision.blurb_report.rating.blurb_hidden is True


def test_an_offsite_next_is_refused_on_the_admin_actions_too(client):
    """`PostActionMixin` is shared with the Mod Center precisely so this guard has one home. Both
    families are tested, because a shared implementation is only safe if both callers are covered."""
    decision = _decision()
    client.force_login(_user('admin'))

    resp = client.post(reverse('admin_reverse_decision', args=[decision.pk]),
                       {'reason': 'fine', 'next': 'https://evil.example.com/'})

    assert 'evil.example.com' not in resp.url


def test_the_page_only_offers_reverse_for_what_the_service_can_undo(client):
    """Offering a button the service refuses is the worst of both: the admin has already typed a
    reason before being told no."""
    from trophies.services import moderation_service

    decision = _decision()
    reversal = moderation_service.reverse_action(decision, _user('admin'), 'undone')
    client.force_login(_user('admin'))

    body = client.get(reverse('admin_decisions')).content.decode()

    assert reverse('admin_reverse_decision', args=[decision.pk]) not in body, (
        'an already-reversed decision still offered Reverse')
    assert reverse('admin_reverse_decision', args=[reversal.pk]) not in body, (
        'a reversal offered a Reverse button of its own')


def test_the_reversible_filter_shows_only_what_can_be_undone(client):
    from trophies.services import moderation_service

    undoable = _decision()
    already = moderation_service.dismiss_game_flag(
        GameFlag.objects.create(game=GameFactory(), reporter=ProfileFactory(is_linked=True),
                                flag_type='delisted'),
        _user('moderator'), 'fine')
    moderation_service.reverse_action(already, _user('admin'), 'undone')
    client.force_login(_user('admin'))

    body = client.get(reverse('admin_decisions') + '?show=reversible').content.decode()

    assert reverse('admin_reverse_decision', args=[undoable.pk]) in body
    assert 'undone' not in body, 'a reversed decision showed up under `reversible`'


def test_a_bad_filter_falls_back_rather_than_500ing(client):
    _decision()
    client.force_login(_user('admin'))

    resp = client.get(reverse('admin_decisions') + '?show=nonsense')

    assert resp.status_code == 200
    assert resp.context['show'] == 'all'


def test_the_log_says_loudly_when_a_reversal_could_not_put_everything_back(client):
    """An admin who believes a reversal fully undid something when it did not will act on that
    belief. It gets an alert, not a tucked-away note."""
    from trophies.services import moderation_service

    flag = GameFlag.objects.create(game=GameFactory(), reporter=ProfileFactory(is_linked=True),
                                   flag_type='delisted')
    approval = moderation_service.approve_game_flag(flag, _user('moderator'), 'delisted')
    flag.game.refresh_from_db()
    flag.game.is_delisted = False                      # somebody else changes it back, later
    flag.game.save(update_fields=['is_delisted'])
    moderation_service.reverse_action(approval, _user('admin'), 'wrong call')
    client.force_login(_user('admin'))

    body = client.get(reverse('admin_decisions')).content.decode()

    assert 'Not everything was put back' in body
    assert 'is_delisted' in body


def test_the_decision_log_does_not_query_per_row(client):
    client.force_login(_user('admin'))
    _decision()

    with CaptureQueriesContext(connection) as few:
        client.get(reverse('admin_decisions'))

    for _ in range(6):
        _decision()

    with CaptureQueriesContext(connection) as many:
        client.get(reverse('admin_decisions'))

    assert len(many.captured_queries) <= len(few.captured_queries) + 2, (
        f'{len(few.captured_queries)} queries for 1 row, {len(many.captured_queries)} for 7')
