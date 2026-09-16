"""`PremiumRequiredMixin` -- the beta gate, which had a hole in it because nothing had mounted it yet.

Every description of what this mixin is for states the cohort as "member + staff". The code checked
membership alone, and since it had zero callers nothing had ever noticed. The first surface to mount
it -- Tiers, Grids & Polls, which ships behind exactly this gate -- would have locked out every
moderator who does not personally subscribe, which is precisely the group that has to reach a gated
page in order to moderate what is on it.

Tested through a throwaway view rather than a real URL, because the mixin is the unit and a route
would drag in whichever surface happened to be mounted this month.

The gate is designed to be DELETED -- ending the beta is removing one class from one base list. There
was a test here claiming to pin that; it asserted a bare `django.views.View` returns 200, which holds
forever no matter what this mixin does. It proved Django works. Removed rather than rewritten: the
property is real but it is not one a test can hold, and a test that cannot fail is worse than none.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory
from django.views import View

from tests.factories import ProfileFactory
from trophies.mixins import PremiumRequiredMixin

pytestmark = pytest.mark.django_db


class _Gated(PremiumRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return HttpResponse('in')


def _ask(user):
    request = RequestFactory().get('/anything/')
    request.user = user
    return _Gated.as_view()(request)


def _member():
    profile = ProfileFactory(is_linked=True)
    profile.user_is_premium = True
    profile.save(update_fields=['user_is_premium'])
    return profile.user


def _team(*, staff=False, moderator=False):
    """`is_moderator` is a PROPERTY over `role`, not a field -- the role is the stored thing."""
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    user.is_staff = staff
    user.role = 'moderator' if moderator else user.role
    user.save(update_fields=['is_staff', 'role'])
    return user


def test_a_member_gets_in():
    assert _ask(_member()).status_code == 200


def test_a_moderator_who_does_not_subscribe_gets_in():
    """THE HOLE. A moderator is not a customer, and the beta they are asked to watch over was
    unreachable for them."""
    assert _ask(_team(moderator=True)).status_code == 200


def test_staff_who_do_not_subscribe_get_in():
    assert _ask(_team(staff=True)).status_code == 200


def test_a_plain_hunter_is_sent_to_the_beta_page():
    """Not a 403: the page exists and they may have it later, which is a different answer from "no"."""
    resp = _ask(ProfileFactory(is_linked=True).user)

    assert resp.status_code == 302
    assert resp['Location'] == '/beta-access/'


def test_a_deactivated_moderator_does_not_keep_the_key():
    """`is_mod_or_admin` requires `is_active`, and this is the test that stops somebody "simplifying"
    the gate into a bare `is_staff or is_moderator` later. `core/previews.py` has the same pin for
    the same reason."""
    user = _team(moderator=True)
    user.is_active = False
    user.save(update_fields=['is_active'])

    assert _ask(user).status_code == 302


def test_a_signed_out_visitor_goes_to_the_login_and_not_the_beta_page():
    """They may well be a member. Sending them to "you need a membership" would be a lie told to
    somebody who only needs to sign in."""
    resp = _ask(AnonymousUser())

    assert resp.status_code == 302
    assert '/beta-access/' not in resp['Location']
