"""The notification system is hidden pending its rebuild (2026-08).

Hidden, not deleted. Every model, every row and every PRODUCER is intact -- sync still queues a platinum
notification, badges still consolidate one, donations and subscriptions still emit theirs. What is gone is
the surface: the inbox, the staff compose pages, the API, and the bell. The producers are the half a
rebuilt system keeps, so deleting them would mean writing them again later in `psn_api_service`,
`token_keeper`, `donation_service` and `subscription_service` -- the four files least worth re-opening.

What this pins is that nothing leads INTO it, because the ways a parked system leaks back are all quiet:
a bell nobody re-checked, a script still fetching on every page load, an API still accepting writes into a
system with no door.

Modelled on `test_lists_hidden.py`, which is the house pattern for this.
"""
import ast
import re
import weakref
from pathlib import Path

import pytest
from django.urls import resolve, reverse

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

PAGES = [
    '/notifications/',
    '/staff/notifications/',
    '/staff/notifications/history/',
    '/staff/notifications/scheduled/',
    # The one that CAPTURES an argument, and the only one that can 500. `RedirectView` forwards captured
    # kwargs into `reverse()`, so `pattern_name='home'` -- which takes none -- raises NoReverseMatch on
    # every hit, ungated by any auth check. It has to be the `url='/'` form, and this row is what says so.
    '/staff/notifications/scheduled/5/cancel/',
]

#: Withdrawn outright, not redirected. The rating endpoint is in here on purpose: it was the SECOND
#: server-side writer of UserConceptRating, and closing it leaves `GroupRatingView` as the only one.
WITHDRAWN_API = [
    '/api/v1/notifications/',
    '/api/v1/notifications/mark-all-read/',
    '/api/v1/notifications/bulk-delete/',
    '/api/v1/notifications/1/',
    '/api/v1/notifications/1/read/',
    '/api/v1/notifications/1/rating/',
    '/api/v1/admin/notifications/send/',
    '/api/v1/admin/notifications/preview/',
    '/api/v1/admin/notifications/target-count/',
]


@pytest.mark.parametrize('url', PAGES)
def test_every_notification_page_sends_you_home(client, url):
    resp = client.get(url)

    assert resp.status_code == 302, f'{url} still renders (got {resp.status_code})'
    assert resp['Location'] == '/', f'{url} redirects to {resp["Location"]}, not the homepage'


@pytest.mark.parametrize('url', PAGES)
def test_the_redirect_is_temporary_so_it_can_be_taken_back(url):
    """A 301 is cached by the browser indefinitely. Using one here would keep sending people to the
    homepage long after the rebuilt system ships -- and specifically the people who used notifications
    most, because they are the ones holding the bookmarks."""
    assert resolve(url).func.view_initkwargs['permanent'] is False


def test_the_url_names_still_reverse():
    """The parked views redirect to `admin_notification_center` in eleven places and the parked templates
    reverse both names. None of that is reachable, but keeping the names resolvable is what makes the
    restore a matter of putting routes back rather than editing the parked code."""
    assert reverse('notification_inbox') == '/notifications/'
    assert reverse('admin_notification_center') == '/staff/notifications/'


def test_no_reachable_staff_page_offers_a_door_into_the_hidden_system():
    """These three staff pages are live, routed and linked, and each carried a "Notifications" button in
    its header. A button that silently lands you on the public homepage is worse than no button -- it is
    the same leak the navbar bell was, just further from the front door.

    They are the reason the URL name looked load-bearing. It is not: with the buttons gone, nothing
    reachable reverses it."""
    for rel in ('templates/users/admin/subscription_dashboard.html',
                'templates/fundraiser/fundraiser_admin.html',
                'templates/fundraiser/badge_reveal.html'):
        src = (ROOT / rel).read_text(encoding='utf-8')
        assert 'admin_notification_center' not in src, (
            f'{rel} links into the hidden notification centre again -- that button dead-ends on the '
            f'homepage'
        )


@pytest.mark.parametrize('url', WITHDRAWN_API)
def test_the_api_no_longer_accepts_reads_or_writes(client, url):
    """An endpoint left answering would let anything still holding a reference file rows into a system
    nobody can open, which the rebuild then has to reconcile."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    # A POST to an unrouted path answers 405 rather than 404 on this site: the custom `handler404` is a
    # view that only allows GET/HEAD/OPTIONS, so it rejects the METHOD before it ever reports the missing
    # route. Compared against a control path that has never existed, so this asserts "answers like an
    # unrouted path" rather than a specific code -- pinning 404 here would be pinning a quirk.
    control = client.post('/api/v1/definitely-not-a-route/', {}).status_code

    assert client.get(url).status_code == 404, f'{url} is still routed'
    assert client.post(url, {}).status_code == control, f'{url} still accepts writes'


def test_the_staff_user_picker_survived_the_withdrawal(client):
    """The one endpoint deliberately left routed. `badge_creation.html` uses it as its user picker, so
    withdrawing it with the rest would have silently broken a staff tool that has nothing to do with
    notifications. It belongs somewhere neutral; rehoming it is a follow-up."""
    src = (ROOT / 'templates' / 'trophies' / 'badge_creation.html').read_text(encoding='utf-8')
    assert '/api/v1/admin/notifications/user-search/' in src

    staff = ProfileFactory(is_linked=True)
    staff.user.is_staff = True
    staff.user.save(update_fields=['is_staff'])
    client.force_login(staff.user)

    # 200, not merely "not 404". The point of the exception is that the picker still WORKS; a routed but
    # broken endpoint would satisfy a `!= 404` and leave the staff tool just as dead.
    assert client.get('/api/v1/admin/notifications/user-search/?q=x').status_code == 200


def test_nothing_in_the_chrome_rings_the_bell():
    """The navbar bell was the only user-facing door, and `notifications.js` was loaded by base.html on
    EVERY page -- it fetched an unread count per page load for every authed visitor."""
    def source(rel):
        src = (ROOT / rel).read_text(encoding='utf-8')
        # Strip the notes explaining the removal, which naturally name the thing they removed.
        return re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', src, flags=re.S)

    navbar = source('templates/partials/navbar.html')
    assert 'notification-dropdown' not in navbar, 'the notification dropdown is back in the navbar'
    assert 'notification-badge' not in navbar, 'the unread badge is back in the navbar'
    assert 'notification_inbox' not in navbar, 'the navbar links into the hidden inbox again'

    base = source('templates/base.html')
    assert 'notifications.js' not in base, 'base.html ships the notification poller again'


def test_the_dead_controller_is_gone_not_just_unreferenced():
    """330 lines whose only entry points were the bell and a per-page-load count fetch. Left in place it
    would download and parse on every page while binding nothing."""
    assert not (ROOT / 'static' / 'js' / 'notifications.js').exists()


def test_the_platinum_confetti_went_with_it():
    """`celebratePlatinum` fired from `notifications.js` polling for unread `platinum_earned` rows, so the
    confetti was a side effect of an INBOX rather than of earning anything -- it went off on whatever page
    you happened to open next. Dropped deliberately; the moment belongs to the rebuild, fired from the
    earning. `loadConfetti` / `fireSideConfetti` stay, because easter-eggs.js and reel-spinner.js use them.
    """
    src = (ROOT / 'static' / 'js' / 'celebrations.js').read_text(encoding='utf-8')
    body = re.sub(r'/\*.*?\*/', '', src, flags=re.S)

    assert 'celebratePlatinum' not in body
    assert 'loadConfetti' in body and 'fireSideConfetti' in body


def test_no_url_conf_imports_a_view_it_no_longer_routes():
    """A name imported with nothing using it is the residue a teardown leaves, and it is what makes the
    next person think the routes are still there."""
    for rel in ('plat_pursuit/urls.py', 'api/urls.py'):
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
        imported = {a.asname or a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        # Scoped by WHERE the name came from, not by what it is spelled. Two of the nine withdrawn views
        # -- AdminTargetCountView, AdminUserSearchView -- have no "Notification" in them, so a substring
        # filter would wave them straight back through.
        from_views = {a.asname or a.name for n in ast.walk(tree)
                      if isinstance(n, ast.ImportFrom) and 'notification_views' in (n.module or '')
                      for a in n.names}
        dead = sorted((imported & from_views) - used)
        assert not dead, f'{rel} imports {dead} but routes none of them'


def test_the_chokepoint_still_writes():
    """The model and the service beneath every producer. Necessary, not sufficient -- see the two tests
    below, which are what actually pin the producers themselves."""
    from notifications.models import Notification
    from notifications.services.notification_service import NotificationService

    profile = ProfileFactory(is_linked=True)
    NotificationService.create_notification(
        recipient=profile.user,
        notification_type='system_alert',
        title='Still writing',
        message='The door is shut, the plumbing is not.',
    )

    assert Notification.objects.filter(recipient=profile.user, title='Still writing').exists()


# The producer call sites, by the file that owns them. This is the half of the system a rebuild KEEPS,
# and the whole justification for hiding rather than deleting -- so it is the half worth pinning.
PRODUCERS = [
    ('trophies/services/psn_api_service.py', 'notify_new_platinum'),
    ('trophies/services/badge_refresh_service.py', 'DeferredNotificationService'),
    ('fundraiser/services/donation_service.py', '_send_donation_notification'),
    ('users/services/subscription_service.py', 'NotificationService.create_notification'),
    ('notifications/signals.py', 'notify_platinum_earned'),
]


@pytest.mark.parametrize('rel,call', PRODUCERS)
def test_the_producers_still_call_out(rel, call):
    """Exercising `NotificationService` directly proves the service works, which nobody doubted. What a
    teardown actually breaks is the CALLER -- and every one of these lives in a file the hiding commit
    never touched, which is exactly why a later cleanup pass is the thing to guard against."""
    src = (ROOT / rel).read_text(encoding='utf-8')
    body = re.sub(r'^\s*#.*$', '', src, flags=re.M)

    assert call in body, (
        f'{rel} no longer calls {call} -- the notification SURFACE is hidden, but its producers are the '
        f'half the rebuild keeps'
    )


def test_the_signal_receivers_are_actually_connected():
    """`NotificationsConfig.ready()` importing `notifications.signals` is what wires the receivers. Asserting
    the app is in INSTALLED_APPS only proves the import was attempted -- this walks the live receiver
    registry, so an unhooked or renamed receiver fails here rather than going quiet in production."""
    from django.db.models.signals import post_save

    connected = set()
    for entry in post_save.receivers:
        ref = entry[1]
        fn = ref() if isinstance(ref, weakref.ReferenceType) else ref
        connected.add(getattr(fn, '__module__', None))

    assert 'notifications.signals' in connected, (
        'no post_save receiver is registered from notifications.signals -- the producers are still in the '
        'tree but nothing triggers them'
    )
