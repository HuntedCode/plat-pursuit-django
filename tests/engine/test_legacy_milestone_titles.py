"""The legacy milestone engine's title fallout (Lane 2 Step 3).

Migration 0282 deleted the titles its metric LADDERS granted and kept the one-off MANUAL awards
(fundraiser patron, easter eggs). Those survivors have no source row anymore -- `source_id` is a
plain integer, not an FK -- so these pin that they still resolve and still render as "Special".
"""
import pytest

from trophies.models import Title, UserTitle
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def test_only_the_three_manual_awards_survive_as_special(client):
    """0283 cleanup: a milestone-sourced title that isn't one of the 3 manual awards must not
    linger as a bogus 'Special'. 0282 missed rows with a NULL/dangling source_id; this pins the
    end state the pair of migrations must produce."""
    from trophies.models import UserTitle as UT

    profile = ProfileFactory()
    keeper = Title.objects.create(name='Case Hardened')          # a real manual award
    ladder = Title.objects.create(name='Plat Hunter')            # a retired metric-ladder title
    UT.objects.create(profile=profile, title=keeper, source_type='milestone', source_id=None)
    UT.objects.create(profile=profile, title=ladder, source_type='milestone', source_id=None)

    # Simulate the 0283 predicate (the migration itself runs against historical models).
    UT.objects.filter(source_type='milestone').exclude(
        title__name__in=['Patron of the Arts', 'Fastest Plat in the West', 'Case Hardened']
    ).delete()

    client.force_login(profile.user)
    resp = client.get('/titles/')

    assert [e['title'].name for e in resp.context['special_titles']] == ['Case Hardened']
    assert not UT.objects.filter(title=ladder).exists()


def test_surviving_milestone_title_resolves_without_a_source_row():
    """_resolve_title_source must not crash / must describe a sourceless milestone title."""
    from api.review_views import _resolve_title_source

    profile = ProfileFactory()
    title = Title.objects.create(name='Badge Artwork Patron')
    ut = UserTitle.objects.create(
        profile=profile, title=title,
        source_type='milestone', source_id=999999,   # dangling: the Milestone row is gone
    )

    assert _resolve_title_source(ut) == 'Earned from a special award'


def test_titles_page_shows_surviving_awards_as_special(client):
    """The Titles page groups sourceless milestone titles under Special, without a source row."""
    profile = ProfileFactory()
    title = Title.objects.create(name='Unboxed!')
    UserTitle.objects.create(
        profile=profile, title=title, source_type='milestone', source_id=999999,
    )
    client.force_login(profile.user)

    resp = client.get('/titles/')

    assert resp.status_code == 200
    specials = resp.context['special_titles']
    assert [e['title'].name for e in specials] == ['Unboxed!']
    assert specials[0]['source'] is None       # no live source row to describe
    assert specials[0]['earned'] is True
    assert resp.context['total_earned'] == 1


def test_milestone_title_also_granted_by_a_badge_is_not_double_counted(client):
    """A surviving milestone title whose Title a live badge also grants belongs to the badge
    section only -- listing it in both would double-count total_earned."""
    from tests.factories import BadgeFactory

    profile = ProfileFactory()
    title = Title.objects.create(name='Case Hardened')
    BadgeFactory(title=title, is_live=True)      # a live badge grants the same Title
    UserTitle.objects.create(
        profile=profile, title=title, source_type='milestone', source_id=999999,
    )
    client.force_login(profile.user)

    resp = client.get('/titles/')

    assert resp.status_code == 200
    assert resp.context['special_titles'] == []                     # not duplicated here
    assert [e['title'].name for e in resp.context['badge_titles']] == ['Case Hardened']
    assert resp.context['total_earned'] == 1                        # counted exactly once
