"""Django admin belongs to the site owner, not to the admin team.

Django's own gate is `is_staff`, and the 2026-08 role split wires `is_staff` to `role='admin'` --
so every Administrator could reach `/admin/`. That is by a distance the most dangerous surface here:
roughly ninety bulk actions that mutate live data, most writing no audit entry, plus raw edit access
to every model. The admin TEAM uses `/staff/`, where every action is logged with a name and a reason.

The lever is `is_superuser` rather than a hardcoded username or id: Django-native, grantable and
revocable from the admin itself, and it does not rot when an email changes.
"""
import pytest
from django.urls import reverse

from tests.factories import UserFactory

pytestmark = pytest.mark.django_db

#: The index, a changelist, and an add form. Each is reached through `AdminSite.admin_view`, so a
#: gate on the site covers all three -- but they are listed separately because a per-ModelAdmin
#: approach would have covered only the middle one.
ADMIN_URLS = ['/admin/', '/admin/trophies/gameflag/', '/admin/users/customuser/add/']


def _assert_bounced_out_of_the_admin(resp, url):
    """Turned away means LEFT the admin, not "did not see one particular heading".

    The first cut asserted `'Site administration' not in body`, which is a string that appears only
    on the index and a changelist -- so the add-form parameter could not fail however open the gate
    was. It only appeared to fail under mutation because that view raises a 500 for unrelated
    reasons. Landing outside `/admin/` is true of every admin URL and false of every open one.
    """
    assert resp.redirect_chain, f'{url} answered directly instead of turning anyone away'
    final = resp.redirect_chain[-1][0]
    assert not final.startswith('/admin/'), f'{url} left them inside the admin, at {final}'

    body = resp.content.decode()
    for marker in ('Site administration', 'Select game flag to change', 'id="user-tools"'):
        assert marker not in body, f'{url} rendered the admin shell ({marker})'



def _admin_user(with_permissions=True):
    """An Administrator: `role='admin'`, which the lockstep turns into `is_staff`. NOT a superuser.

    Granted EVERY model permission by default, and that is the point of the fixture rather than a
    detail. Django's admin refuses a changelist to a staff user holding no permissions all by
    itself, so a permission-less fixture would be turned away by machinery that was always there --
    and every test here would pass with our gate removed. Loading the permissions is what makes
    these tests about the SITE gate: without it this user reaches everything.
    """
    from django.contrib.auth.models import Permission

    user = UserFactory()
    user.role = 'admin'
    user.save()
    assert user.is_staff is True, 'the role lockstep changed'
    assert user.is_superuser is False
    if with_permissions:
        user.user_permissions.set(Permission.objects.all())
        user = type(user).objects.get(pk=user.pk)      # drop the permission cache
    return user


def _owner():
    user = UserFactory()
    user.is_superuser = user.is_staff = True
    user.save()
    return user


# ── who is turned away ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url', ADMIN_URLS)
def test_an_administrator_is_turned_away_from_django_admin(client, url):
    """The whole point. `is_staff` is true for them, which is exactly what Django would have let in."""
    client.force_login(_admin_user())

    resp = client.get(url, follow=True)

    _assert_bounced_out_of_the_admin(resp, url)


@pytest.mark.parametrize('url', ADMIN_URLS)
def test_a_moderator_is_turned_away(client, url):
    # `role` set after creation, not passed to the factory: `UserFactory` routes through
    # `create_user` and would silently drop it, leaving this testing an ordinary hunter twice.
    moderator = UserFactory()
    moderator.role = 'moderator'
    moderator.save()
    assert moderator.is_moderator is True
    client.force_login(moderator)

    resp = client.get(url, follow=True)

    _assert_bounced_out_of_the_admin(resp, url)


@pytest.mark.parametrize('url', ADMIN_URLS)
def test_an_ordinary_hunter_is_turned_away(client, url):
    client.force_login(UserFactory())

    resp = client.get(url, follow=True)

    _assert_bounced_out_of_the_admin(resp, url)


@pytest.mark.parametrize('url', ADMIN_URLS)
def test_a_signed_out_visitor_is_turned_away(client, url):
    resp = client.get(url, follow=True)

    assert not resp.content.decode().count('id="user-tools"')
    assert '/accounts/login' in resp.redirect_chain[-1][0], (
        'a stranger should reach the site login, not an admin one')


def test_an_administrator_cannot_POST_a_bulk_action(client):
    """Status codes are not the assertion; the database is. A gate that redirects AFTER running the
    action would pass a redirect test."""
    from tests.factories import GameFactory, ProfileFactory
    from trophies.models import GameFlag

    flag = GameFlag.objects.create(game=GameFactory(), reporter=ProfileFactory(is_linked=True),
                                   flag_type='delisted')
    client.force_login(_admin_user())

    client.post('/admin/trophies/gameflag/',
                {'action': 'approve_selected', '_selected_action': [str(flag.pk)]}, follow=True)

    flag.refresh_from_db()
    assert flag.status == 'pending', 'an Administrator ran a Django-admin bulk action'
    assert flag.game.is_delisted is False


def test_a_deactivated_superuser_is_turned_away(client):
    owner = _owner()
    client.force_login(owner)
    owner.is_active = False
    owner.save()

    resp = client.get('/admin/', follow=True)

    _assert_bounced_out_of_the_admin(resp, '/admin/')


def test_the_gate_itself_refuses_a_deactivated_superuser():
    """Directly, because the request path cannot reach it: `ModelBackend.get_user` turns a
    deactivated account into AnonymousUser before `has_permission` is ever consulted, so the test
    above passes with the `is_active` clause deleted. This is the one that exercises it."""
    from django.contrib import admin

    class _Deactivated:
        is_active, is_staff, is_superuser = False, True, True

    class _Request:
        user = _Deactivated()

    assert admin.site.has_permission(_Request()) is False


def test_the_gate_refuses_a_superuser_who_is_not_staff():
    """The corner where code, login form and docs used to disagree: Django's admin login form
    refuses a non-staff account outright, so admitting one here meant letting somebody in through a
    door the front of which would not open."""
    from django.contrib import admin

    class _NotStaff:
        is_active, is_staff, is_superuser = True, False, True

    class _Request:
        user = _NotStaff()

    assert admin.site.has_permission(_Request()) is False


# ── there is no admin login form ─────────────────────────────────────────────────────────────────
#
# `login/` is the ONE entry in `AdminSite.get_urls()` not wrapped in `admin_view`, so narrowing
# `has_permission` did nothing to it. Left rendering, it is a second credential endpoint that
# `ACCOUNT_RATE_LIMITS` does not throttle and Cloudflare does not front.

def test_the_admin_login_form_does_not_exist(client):
    resp = client.get('/admin/login/')

    assert resp.status_code == 302
    assert '/accounts/login' in resp.url, 'the admin still has a login form of its own'


def test_the_admin_login_form_cannot_be_used_to_guess_passwords(client):
    """A POST used to authenticate anybody who knew a real password, unthrottled, minting the same
    session cookie the rest of the site uses. It now redirects without reading the credentials."""
    owner = _owner()
    owner.set_password('correct-horse-battery')
    owner.save()

    resp = client.post('/admin/login/',
                       {'username': owner.email, 'password': 'correct-horse-battery'})

    assert resp.status_code == 302
    assert '_auth_user_id' not in client.session, 'the admin login form authenticated somebody'


def test_the_admin_login_no_longer_answers_whether_an_account_is_staff(client):
    """It answered a 302 for a privileged GET and a 200 otherwise -- enough to confirm "this address
    is staff" in one unauthenticated request."""
    signed_out = client.get('/admin/login/')

    client.force_login(_admin_user())
    as_admin = client.get('/admin/login/')

    assert signed_out.status_code == as_admin.status_code == 302


def test_an_authenticated_visitor_is_not_bounced_in_a_loop(client):
    """Sending a signed-in visitor to a login page with `next=/admin/` loops: the form redirects an
    already-authenticated user straight back to `next`. Signing in again cannot grant a permission
    they do not have."""
    client.force_login(_admin_user())

    resp = client.get('/admin/', follow=True)      # raises RedirectCycleError if it loops

    assert resp.redirect_chain[-1][0] == reverse('admin_hub'), (
        'an Administrator should land on the tools that ARE theirs')


def test_a_hunter_who_wanders_into_the_admin_is_sent_home(client):
    client.force_login(UserFactory())

    resp = client.get('/admin/', follow=True)

    assert resp.redirect_chain[-1][0] == '/'


def test_the_admin_login_next_cannot_bounce_somebody_offsite(client):
    resp = client.get('/admin/login/?next=https://evil.example.com/')

    assert 'evil.example.com' not in resp.url


# ── who gets in ──────────────────────────────────────────────────────────────────────────────────

def test_the_owner_still_gets_in(client):
    """The gate has to keep the owner in, or the site is one lockout from unmaintainable."""
    client.force_login(_owner())

    resp = client.get('/admin/')

    assert resp.status_code == 200
    assert 'Site administration' in resp.content.decode()


def test_the_owner_can_still_reach_a_changelist(client):
    client.force_login(_owner())

    assert client.get('/admin/trophies/gameflag/').status_code == 200


# ── the door is not advertised ───────────────────────────────────────────────────────────────────

def test_the_admin_hub_hides_django_admin_from_an_administrator(client):
    """Turning somebody away from a door you still show them is worse than not showing it: it
    advertises the door, and being refused reads as a fault rather than a boundary."""
    client.force_login(_admin_user())

    body = client.get(reverse('admin_hub')).content.decode()

    assert 'Django admin' not in body
    assert 'href="/admin/"' not in body


def test_the_admin_hub_still_offers_it_to_the_owner(client):
    client.force_login(_owner())

    body = client.get(reverse('admin_hub')).content.decode()

    assert 'href="/admin/"' in body


def test_nothing_else_on_the_site_links_to_django_admin():
    """A link in shared chrome would reach every Administrator regardless of the gate.

    Every spelling, not just the one I happened to write. `{% url 'admin:index' %}` is the IDIOMATIC
    way somebody would add this link, and the first cut of this guard did not look for it -- nor for
    single quotes, nor for JS. Exempted by PATH, not by filename: matching `admin_hub.html` anywhere
    would exempt a future copy of it in another directory.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    allowed = {'templates/staff/admin_hub.html'}
    patterns = [re.compile(p) for p in (
        r'href=["\']/admin/',
        r'{%\s*url\s+["\']admin:',
        r'["\']/admin/["\']',
    )]

    offenders = []
    for folder, suffix in (('templates', '*.html'), ('static/js', '*.js')):
        for path in (root / folder).rglob(suffix):
            relative = path.relative_to(root).as_posix()
            if relative in allowed:
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            if any(pattern.search(text) for pattern in patterns):
                offenders.append(relative)

    assert not offenders, f'linking to Django admin: {offenders}'

# ── the gate is on the SITE, not on individual models ────────────────────────────────────────────

def test_the_gate_is_the_admin_site_itself():
    """Per-ModelAdmin permissions would have to be repeated on every one of ~140 registrations and
    would still leave the index, the login view and any app registered later wide open."""
    from django.contrib import admin

    from core.admin_site import SuperuserOnlyAdminSite

    assert isinstance(admin.site, SuperuserOnlyAdminSite)


def test_every_registered_model_lives_on_the_narrowed_site():
    """`@admin.register` targets `admin.site`, so swapping the site is what makes this true of
    registrations nobody has read -- including any a future dependency adds."""
    from django.contrib import admin

    assert len(admin.site._registry) > 100, 'the registry moved somewhere else'
