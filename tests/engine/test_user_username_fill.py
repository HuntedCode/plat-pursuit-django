"""`CustomUser.save()` fills AbstractUser's unique `username` when it is empty.

Nothing signs in with it, but it is still a unique column: an empty one saves once, then the next empty one breaks
the constraint. Signup filled it through allauth and the admin's Add user form did the same by hand, but
`createsuperuser` and `create_user` did not, so a second superuser could not be made.
"""
import pytest
from django.core.management import call_command
from django.utils import timezone

from users.models import CustomUser

pytestmark = pytest.mark.django_db


def test_two_accounts_from_create_user_never_collide_on_username():
    """Same local part on purpose: allauth derives the username from it, so the second must be made unique."""
    first = CustomUser.objects.create_user('twin@one.example', 'a-long-test-passphrase-91')
    second = CustomUser.objects.create_user('twin@two.example', 'a-long-test-passphrase-91')

    assert first.username and second.username
    assert first.username != second.username


def test_createsuperuser_can_be_run_twice(monkeypatch):
    monkeypatch.setenv('DJANGO_SUPERUSER_PASSWORD', 'a-long-test-passphrase-91')
    call_command('createsuperuser', interactive=False, email='boss@one.example', verbosity=0)
    call_command('createsuperuser', interactive=False, email='boss@two.example', verbosity=0)

    usernames = list(CustomUser.objects.filter(is_superuser=True).values_list('username', flat=True))
    assert len(usernames) == 2
    assert all(usernames) and len(set(usernames)) == 2, usernames


def test_an_older_empty_row_is_filled_by_a_narrow_save():
    """A row made before the fix: a sign-in's `last_login` write names only that field, so the fill must add
    `username` to `update_fields` or it is computed and never stored."""
    user = CustomUser.objects.create_user('legacy@example.com', 'a-long-test-passphrase-91')
    CustomUser.objects.filter(pk=user.pk).update(username='')
    user.refresh_from_db()

    user.last_login = timezone.now()
    user.save(update_fields=['last_login'])

    user.refresh_from_db()
    assert user.username, 'the username was filled in memory but not written'
    assert user.last_login is not None
