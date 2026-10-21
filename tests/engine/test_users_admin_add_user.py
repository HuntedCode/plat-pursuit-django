"""The Django admin's "Add user" page.

It used to 500: `CustomUserAdmin.add_form` was the allauth signup form, which is not a ModelForm, so the admin
could not build the page at all. These pin the page, the account it makes, and the hidden `username` column
that allauth fills on signup and the admin must fill the same way.
"""
import pytest
from django.urls import reverse

from tests.factories import UserFactory
from users.admin import CustomUserAdmin
from users.forms import AdminAddUserForm
from users.models import CustomUser

pytestmark = pytest.mark.django_db

ADD_URL = reverse('admin:users_customuser_add')
PASSWORD = 'a-long-test-passphrase-91'


@pytest.fixture
def staff_client(client):
    client.force_login(UserFactory(is_staff=True, is_superuser=True))
    return client


def _add(client, email, **extra):
    data = {
        'email': email,
        'user_timezone': 'UTC',
        'usable_password': 'true',
        'password1': PASSWORD,
        'password2': PASSWORD,
        **extra,
    }
    return client.post(ADD_URL, data)


def test_the_admin_adds_users_with_its_own_form_not_the_signup_form():
    assert CustomUserAdmin.add_form is AdminAddUserForm


def test_the_add_page_renders(staff_client):
    resp = staff_client.get(ADD_URL)

    assert resp.status_code == 200
    assert b'name="email"' in resp.content
    assert b'name="password1"' in resp.content
    assert b'name="usable_password"' in resp.content, 'the "password-based sign in" choice is not on the page'


def test_adding_a_user_makes_an_account_that_can_sign_in(staff_client):
    resp = _add(staff_client, 'New.Hunter@Example.com')

    assert resp.status_code == 302, resp.content[:2000]
    user = CustomUser.objects.get(email='new.hunter@example.com')
    assert user.check_password(PASSWORD)
    assert user.username, 'the unique username column was left empty'


def test_two_added_users_never_collide_on_username(staff_client):
    """Same local part on purpose: allauth derives the username from it, so the second must be made unique."""
    assert _add(staff_client, 'twin@one.example').status_code == 302
    assert _add(staff_client, 'twin@two.example').status_code == 302

    usernames = list(CustomUser.objects.filter(email__startswith='twin@').values_list('username', flat=True))
    assert len(usernames) == 2
    assert len(set(usernames)) == 2, usernames
    assert all(usernames), usernames


def test_an_email_already_in_use_is_refused_whatever_its_case(staff_client):
    """The stored row is mixed case on purpose: `create_user` keeps the local part's case, so an exact match on
    the lowercased input would miss it and two accounts would share one address."""
    UserFactory(email='Taken@example.com')

    resp = _add(staff_client, 'taken@example.com')

    assert resp.status_code == 200
    assert b'This email is already in use.' in resp.content
    assert CustomUser.objects.filter(email__iexact='taken@example.com').count() == 1


def test_mismatched_passwords_are_refused(staff_client):
    resp = _add(staff_client, 'typo@example.com', password2='not-the-same-passphrase-17')

    assert resp.status_code == 200
    assert 'password2' in resp.context['adminform'].form.errors, 'refused for some other reason'
    assert not CustomUser.objects.filter(email='typo@example.com').exists()


def test_password_based_sign_in_can_be_left_off(staff_client):
    resp = _add(staff_client, 'nopass@example.com', usable_password='false', password1='', password2='')

    assert resp.status_code == 302, resp.content[:2000]
    assert not CustomUser.objects.get(email='nopass@example.com').has_usable_password()
