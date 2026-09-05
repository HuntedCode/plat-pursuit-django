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

    body = resp.content.decode()
    assert 'Site administration' not in body, 'an Administrator reached the Django admin index'
    assert 'Select game flag to change' not in body, 'an Administrator reached a changelist'
    # Django hands an unauthorised-but-authenticated visitor its login page rather than a 403.
    assert 'admin/login' in resp.redirect_chain[-1][0] if resp.redirect_chain else True


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

    assert 'Site administration' not in resp.content.decode()


@pytest.mark.parametrize('url', ADMIN_URLS)
def test_an_ordinary_hunter_is_turned_away(client, url):
    client.force_login(UserFactory())

    resp = client.get(url, follow=True)

    assert 'Site administration' not in resp.content.decode()


@pytest.mark.parametrize('url', ADMIN_URLS)
def test_a_signed_out_visitor_is_turned_away(client, url):
    resp = client.get(url, follow=True)

    assert 'Site administration' not in resp.content.decode()


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

    assert 'Site administration' not in resp.content.decode()


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
    """A link in a shared template would reach every Administrator regardless of the gate."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / 'templates'
    offenders = []
    for template in root.rglob('*.html'):
        text = template.read_text(encoding='utf-8', errors='ignore')
        if 'href="/admin/' in text and template.name != 'admin_hub.html':
            offenders.append(str(template.relative_to(root)))

    assert not offenders, f'templates linking to Django admin: {offenders}'


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
