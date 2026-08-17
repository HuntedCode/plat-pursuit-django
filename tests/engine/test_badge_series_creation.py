"""The staff badge-authoring tool at `/staff/badge-create/` (restored 2026-08).

The pre-cutover page created four legacy tier `Badge` rows. It was deleted in 5b because Django admin
covers the new models -- which it does, but authoring one series there is a seven-page-load click-path
with three raw-ID popup lookups, so "covered" was not the same as "usable". This is the rebuilt version:
one form producing a `BadgeSeries` plus one `GroupBadge` per checked edition.

Stages are deliberately out of scope (they stay in `StageAdmin`, which owns the concept autocomplete and
bundle-overlap validation), so nothing here asserts anything about them beyond the orphan-slug hint.
"""
import pytest
from django.urls import reverse

from trophies.models import BadgeSeries, GroupBadge
from tests.factories import (
    BadgeSeriesFactory, PlatformGroupFactory, ProfileFactory, StageFactory,
)

pytestmark = pytest.mark.django_db

URL = '/staff/badge-create/'


@pytest.fixture
def editions():
    """The two real editions, in display order."""
    return (
        PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=['PS4', 'PS5'], sort_order=10),
        PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS3', 'PSVITA'], sort_order=20),
    )


@pytest.fixture
def staff_client(client):
    profile = ProfileFactory(is_linked=True)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])
    client.force_login(profile.user)
    return client


def _payload(editions, **overrides):
    data = {
        'name': 'Soulsborne',
        'series_slug': 'soulsborne',
        'badge_type': 'series',
        'completion_policy': 'all',
        'min_required': 0,
        'description': '',
        'display_series': '',
        'submitted_by': '',
        'editions': [str(e.pk) for e in editions],
    }
    data.update(overrides)
    return data


# --- access ------------------------------------------------------------------


def test_anonymous_users_are_sent_away(client):
    resp = client.get(URL)
    assert resp.status_code in (302, 403)


def test_a_signed_in_non_staff_user_is_sent_away(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    assert client.get(URL).status_code == 302


def test_staff_can_load_the_form(staff_client, editions):
    assert staff_client.get(URL).status_code == 200


def test_the_route_keeps_its_pre_cutover_name():
    """Staff bookmarks point at `badge_creation`. Renaming it would break them silently -- nobody reports
    a bookmark that 404s, they just stop using the tool."""
    assert reverse('badge_creation') == URL


# --- creation ----------------------------------------------------------------


def test_a_submit_creates_the_series_and_one_badge_per_edition(staff_client, editions):
    ultra, legacy = editions
    resp = staff_client.post(URL, _payload(editions))
    assert resp.status_code == 302

    series = BadgeSeries.objects.get(series_slug='soulsborne')
    assert series.name == 'Soulsborne'
    assert set(series.group_badges.values_list('platform_group__key', flat=True)) == {'ultra-hd', 'legacy-hd'}


def test_editions_start_hidden_unless_asked_for(staff_client, editions):
    """A badge is normally authored, given stages, THEN released. Shipping live by default would put an
    unearnable badge in front of hunters the moment it is created."""
    staff_client.post(URL, _payload(editions))
    assert GroupBadge.objects.filter(is_live=True).count() == 0


def test_release_immediately_makes_them_live(staff_client, editions):
    staff_client.post(URL, _payload(editions, start_live='on'))
    assert GroupBadge.objects.count() == 2
    assert GroupBadge.objects.filter(is_live=False).count() == 0


def test_only_the_checked_editions_are_created(staff_client, editions):
    ultra, _legacy = editions
    staff_client.post(URL, _payload([ultra]))

    series = BadgeSeries.objects.get(series_slug='soulsborne')
    assert list(series.group_badges.values_list('platform_group__key', flat=True)) == ['ultra-hd']


def test_the_slug_is_derived_from_the_name_when_blank(staff_client, editions):
    staff_client.post(URL, _payload(editions, name='Resident Evil: Remakes', series_slug=''))
    assert BadgeSeries.objects.filter(series_slug='resident-evil-remakes').exists()


def test_a_submitter_is_resolved_to_a_profile(staff_client, editions):
    submitter = ProfileFactory(psn_username='ArtPerson')
    staff_client.post(URL, _payload(editions, badge_type='user', submitted_by='artperson'))

    series = BadgeSeries.objects.get(series_slug='soulsborne')
    assert series.submitted_by_id == submitter.id


# --- validation, all of which the model does NOT do --------------------------


def test_a_duplicate_slug_is_a_form_error_not_a_500(staff_client, editions):
    """`series_slug` is unique, so without the form check this raised IntegrityError -- a 500 page instead
    of a sentence telling the author the slug is taken."""
    BadgeSeriesFactory(series_slug='soulsborne', name='Existing')

    resp = staff_client.post(URL, _payload(editions))

    assert resp.status_code == 200                       # re-rendered with errors, not redirected
    assert b'already uses the slug' in resp.content
    assert BadgeSeries.objects.filter(series_slug='soulsborne').count() == 1


def test_a_megamix_without_a_stage_count_is_rejected(staff_client, editions):
    """`min_required=0` under `min_count` means "earned by clearing zero stages" -- it would be granted to
    every hunter on the next evaluation. The model does not enforce the pairing; this is the one place a
    human types it."""
    resp = staff_client.post(URL, _payload(editions, completion_policy='min_count', min_required=0))

    assert resp.status_code == 200
    assert b'at least one required stage' in resp.content
    assert not BadgeSeries.objects.filter(series_slug='soulsborne').exists()


def test_a_stray_min_required_is_zeroed_outside_megamix(staff_client, editions):
    """Storing a number that means nothing invites someone to later believe it does."""
    staff_client.post(URL, _payload(editions, completion_policy='all', min_required=3))
    assert BadgeSeries.objects.get(series_slug='soulsborne').min_required == 0


def test_an_unknown_submitter_is_a_form_error(staff_client, editions):
    """Silently dropping the credit is the alternative, and it is invisible until the person asks why
    they are not on the badge."""
    resp = staff_client.post(URL, _payload(editions, badge_type='user', submitted_by='nobody-here'))

    assert resp.status_code == 200
    assert b'No profile found' in resp.content
    assert not BadgeSeries.objects.filter(series_slug='soulsborne').exists()


def test_submitting_with_no_editions_is_rejected(staff_client, editions):
    """A series with no editions is unearnable: there is no GroupBadge for the engine to evaluate."""
    resp = staff_client.post(URL, _payload([]))

    assert resp.status_code == 200
    assert not BadgeSeries.objects.filter(series_slug='soulsborne').exists()


def test_an_edition_failing_midway_rolls_back_the_series(staff_client, editions, monkeypatch):
    """The whole create is one transaction, and this is the only test that actually exercises that.

    An earlier version of this test posted invalid form data and asserted nothing was created -- which
    passes whether or not the transaction exists, because form validation rejects the request before the
    transaction opens. To reach the rollback the form has to be VALID and a write has to fail partway, so
    the second GroupBadge is made to blow up here.

    A half-made series is worse than none: the slug is then taken, so retrying needs manual cleanup
    first.
    """
    calls = {'n': 0}
    real = GroupBadge.objects.get_or_create

    def explode_on_second(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('disk on fire')
        return real(*args, **kwargs)

    monkeypatch.setattr(GroupBadge.objects, 'get_or_create', explode_on_second)

    resp = staff_client.post(URL, _payload(editions))

    assert resp.status_code == 200, 'a failed create should re-render, not redirect'
    assert calls['n'] == 2, 'fixture is wrong -- expected two edition writes to be attempted'
    assert BadgeSeries.objects.count() == 0, 'the series survived a failed edition write'
    assert GroupBadge.objects.count() == 0, 'the first edition survived the rollback'


def test_invalid_input_creates_nothing(staff_client, editions):
    """The cheaper sibling of the above: rejection happens at the form, before any write."""
    staff_client.post(URL, _payload(editions, completion_policy='min_count', min_required=0))

    assert BadgeSeries.objects.count() == 0
    assert GroupBadge.objects.count() == 0


# --- the orphan-slug hint ----------------------------------------------------


def test_slugs_with_stages_but_no_series_are_surfaced(staff_client, editions):
    """Stages join to a series by STRING, not an FK, so authoring stages first is legitimate -- and a
    typo looks exactly the same until the two are listed side by side."""
    StageFactory(series_slug='authored-first', stage_number=1)
    BadgeSeriesFactory(series_slug='already-has-one')
    StageFactory(series_slug='already-has-one', stage_number=1)

    resp = staff_client.get(URL)

    assert b'authored-first' in resp.content
    assert resp.context['orphan_stage_slugs'] == ['authored-first']


def test_the_recent_list_shows_what_was_just_created(staff_client, editions):
    """The page doubles as confirmation: after a submit you should see the thing you made, with its
    editions and whether they are live."""
    staff_client.post(URL, _payload(editions))
    resp = staff_client.get(URL)

    assert b'Soulsborne' in resp.content
    assert [s.series_slug for s in resp.context['recent_series']] == ['soulsborne']
