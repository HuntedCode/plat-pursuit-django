"""Restricting a hunter from writing.

The riskiest thing in the admin hub, because it is the only part that changes what an ORDINARY
hunter can do, and both its failure modes are silent: block a legitimate write, or fail to block a
restricted one. Neither raises anything; both just quietly do the wrong thing.

So the important test here is not any single gate but `test_a_full_restriction_blocks_every_way_of_writing`,
which posts at every UGC endpoint on the site and asserts on the DATABASE afterwards. A gate that
redirects after writing passes a status-code test.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAction
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory
from trophies.models import BlurbReport, GameFlag, UserConceptRating
from users.models import UserRestriction
from users.services import restriction_service

pytestmark = pytest.mark.django_db


def _admin():
    user = UserFactory()
    user.role = 'admin'
    user.save()
    return user


def _hunter(psn='hunted47'):
    return ProfileFactory(is_linked=True, psn_username=psn, guidelines_agreed=True)


def _restrict(profile, scope='all_ugc', admin=None, **kwargs):
    return restriction_service.apply_restriction(
        profile.user, scope, admin or _admin(), 'spam, third time', **kwargs)


# ── the service ──────────────────────────────────────────────────────────────────────────────────

def test_applying_a_restriction_logs_it_with_a_reason():
    hunter, admin = _hunter(), _admin()

    restriction = restriction_service.apply_restriction(
        hunter.user, 'quick_takes', admin, 'repeated slurs')

    assert restriction.is_live is True
    entry = AdminAction.objects.get(action='restriction_applied')
    assert entry.actor == admin
    assert entry.subject_user == hunter.user, 'the log cannot say who it was done to'
    assert entry.reason == 'repeated slurs'
    assert entry.target_id == str(restriction.pk)


@pytest.mark.parametrize('bad', ['', '   ', 'x'])
def test_a_restriction_refuses_an_empty_reason(bad):
    """Enforced at the service, like every other audited action -- a form guarantees nothing about
    the next caller."""
    hunter = _hunter()

    with pytest.raises(restriction_service.RestrictionError):
        restriction_service.apply_restriction(hunter.user, 'all_ugc', _admin(), bad)

    assert UserRestriction.objects.count() == 0, 'a refused restriction was still applied'
    assert AdminAction.objects.count() == 0


def test_an_unknown_scope_is_refused():
    hunter = _hunter()

    with pytest.raises(restriction_service.RestrictionError):
        restriction_service.apply_restriction(hunter.user, 'everything', _admin(), 'a reason')

    assert UserRestriction.objects.count() == 0


def test_restricting_somebody_already_restricted_says_so():
    """The lock-plus-precondition shape the moderation queue uses. Without it two admins acting at
    once both succeed, and the second writes an entry claiming it restricted somebody who was
    already restricted."""
    hunter = _hunter()
    _restrict(hunter, 'quick_takes')

    with pytest.raises(restriction_service.RestrictionError) as refused:
        _restrict(hunter, 'quick_takes')

    assert 'Already restricted' in str(refused.value)
    assert UserRestriction.objects.count() == 1


def test_a_broad_restriction_blocks_a_narrower_one_being_added():
    """`all_ugc` already covers quick takes, so adding one would be two rows describing one state --
    and lifting either would leave the hunter half-free with no page saying so."""
    hunter = _hunter()
    _restrict(hunter, 'all_ugc')

    with pytest.raises(restriction_service.RestrictionError):
        _restrict(hunter, 'quick_takes')


def test_a_lapsed_restriction_does_not_block_a_new_one():
    """The reason there is NO partial unique constraint on (user, scope): it would cover expired
    rows, which are not lifted but merely over, and refuse to re-restrict a repeat offender."""
    hunter = _hunter()
    old = _restrict(hunter, 'quick_takes', expires_at=timezone.now() + timedelta(days=1))
    UserRestriction.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(days=1))

    fresh = _restrict(hunter, 'quick_takes')

    assert fresh.is_live is True
    assert UserRestriction.objects.count() == 2


def test_lifting_never_edits_the_row_that_applied_it():
    """The applying ENTRY is not touched. An audit trail that can be rewritten is not one."""
    hunter = _hunter()
    restriction = _restrict(hunter)
    applied = AdminAction.objects.get(action='restriction_applied')
    before = {f.name: getattr(applied, f.name) for f in AdminAction._meta.fields}

    restriction_service.lift_restriction(restriction, _admin(), 'appealed, upheld')

    applied.refresh_from_db()
    after = {f.name: getattr(applied, f.name) for f in AdminAction._meta.fields}
    assert before == after, 'the entry that applied the restriction was rewritten'
    lift = AdminAction.objects.get(action='restriction_lifted')
    assert lift.reverses_id == applied.pk, 'the lift does not point at what it undid'


def test_lifting_frees_the_hunter_immediately():
    hunter = _hunter()
    restriction = _restrict(hunter)
    assert restriction_service.is_restricted_from(hunter, 'quick_takes') is True

    restriction_service.lift_restriction(restriction, _admin(), 'appealed, upheld')

    assert restriction_service.is_restricted_from(hunter, 'quick_takes') is False


def test_a_restriction_cannot_be_lifted_twice():
    hunter = _hunter()
    restriction = _restrict(hunter)
    restriction_service.lift_restriction(restriction, _admin(), 'first')

    with pytest.raises(restriction_service.RestrictionError):
        restriction_service.lift_restriction(restriction, _admin(), 'second')

    assert AdminAction.objects.filter(action='restriction_lifted').count() == 1


def test_a_lapsed_restriction_cannot_be_lifted():
    """It is already over. Lifting it would write an entry claiming somebody freed a hunter who was
    free already."""
    hunter = _hunter()
    restriction = _restrict(hunter, expires_at=timezone.now() + timedelta(days=1))
    UserRestriction.objects.filter(pk=restriction.pk).update(
        expires_at=timezone.now() - timedelta(days=1))
    restriction.refresh_from_db()

    with pytest.raises(restriction_service.RestrictionError) as refused:
        restriction_service.lift_restriction(restriction, _admin(), 'setting them free')

    assert 'expired on its own' in str(refused.value)


def test_a_restriction_lapses_by_the_clock_with_nobody_writing_a_row():
    """Expiry is not an event. Nothing runs at the moment a restriction ends, so `is_live` and the
    gate query both have to evaluate it against `now` every time they are asked."""
    hunter = _hunter()
    restriction = _restrict(hunter, expires_at=timezone.now() + timedelta(days=1))
    assert restriction_service.is_restricted_from(hunter, 'quick_takes') is True

    UserRestriction.objects.filter(pk=restriction.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1))

    assert restriction_service.is_restricted_from(hunter, 'quick_takes') is False
    restriction.refresh_from_db()
    assert restriction.is_live is False
    assert restriction.lifted_at is None, 'lapsing is not lifting'


def test_an_indefinite_restriction_does_not_lapse():
    hunter = _hunter()
    _restrict(hunter, expires_at=None)

    assert restriction_service.is_restricted_from(hunter, 'quick_takes') is True


def test_a_restriction_survives_unlinking_and_relinking_psn():
    """The reason it hangs off `CustomUser` and not `Profile`. On the profile, unlinking would be an
    escape hatch."""
    hunter = _hunter()
    _restrict(hunter)
    user = hunter.user

    hunter.is_linked = False
    hunter.save(update_fields=['is_linked'])
    hunter.refresh_from_db()
    hunter.is_linked = True
    hunter.save(update_fields=['is_linked'])

    assert restriction_service.active_scopes(user.pk) == {'all_ugc'}


def test_the_gate_is_one_query():
    """It runs on ordinary hunters' write paths, so it must not grow with how much history an
    account has."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    hunter = _hunter()
    for _ in range(4):
        row = _restrict(hunter, 'quick_takes')
        restriction_service.lift_restriction(row, _admin(), 'and again')
    _restrict(hunter, 'quick_takes')

    with CaptureQueriesContext(connection) as captured:
        restriction_service.is_restricted_from(hunter, 'quick_takes')

    assert len(captured.captured_queries) == 1


def test_an_unrestricted_hunter_is_not_restricted():
    assert restriction_service.is_restricted_from(_hunter(), 'quick_takes') is False
    assert restriction_service.active_scopes(None) == set()


# ── what a restriction actually stops ────────────────────────────────────────────────────────────

def _rate(client, profile, blurb='some words'):
    """POST a rating, with or without words.

    The URL takes the trophy group's STRING id, not the row's pk -- I had the pk here first, which
    404'd. That mattered more than a typo normally would: these tests assert that nothing was
    written, and a 404 writes nothing either, so three "a restricted hunter cannot write" tests were
    passing on a broken URL. The assertion below is what makes this helper honest: it fails loudly if
    the request never reached the endpoint, so the only way to see an empty database is a gate.
    """
    from tests.factories import EarnedTrophyFactory, TrophyFactory
    from trophies.models import ConceptTrophyGroup

    concept = ConceptFactory()
    ConceptTrophyGroup.objects.create(concept=concept, trophy_group_id='default')
    # A platinum in this concept, because `can_rate_group` requires one before anybody may rate the
    # base game at all. Without it the endpoint answers 403 for EVERY hunter -- so the sweep below
    # would have proved a restriction blocks writing when what actually blocked it was not having
    # played the game.
    platinum = TrophyFactory(game=GameFactory(concept=concept), trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=platinum, earned=True)
    response = client.post(
        f'/api/v1/ratings/{concept.pk}/group/default/rate/',
        {'difficulty': 5, 'grindiness': 5, 'hours_to_platinum': 20, 'fun_ranking': 8,
         'overall_rating': 4.0, 'recommendation': 'worth_it', 'blurb': blurb},
        content_type='application/json')

    # 404 and 400 both write nothing, so either would let a "restricted hunter cannot write" test
    # pass while proving nothing at all. Both were hit while writing these: first a wrong URL, then
    # a missing required field. The helper refuses to be that kind of witness.
    assert response.status_code not in (404, 400), (
        f'the rating endpoint refused the fixture itself ({response.status_code}: '
        f'{response.content[:200]!r}), so "nothing was written" proves nothing')
    return response


def test_a_restricted_hunter_cannot_write_a_quick_take(client):
    hunter = _hunter()
    _restrict(hunter, 'quick_takes')
    client.force_login(hunter.user)

    _rate(client, hunter, blurb='let me through')

    assert not UserConceptRating.objects.exclude(blurb='').exists(), 'the words got through'


def test_a_restricted_hunter_can_still_rate_the_numbers(client):
    """The scores are not what was restricted. Silently dropping them would rewrite the game's
    averages as a side effect of a moderation decision about somebody's prose -- the same principle
    that keeps `blurb_hidden` separate from the blurb."""
    hunter = _hunter()
    _restrict(hunter, 'quick_takes')
    client.force_login(hunter.user)

    _rate(client, hunter, blurb='')

    rating = UserConceptRating.objects.get()
    assert rating.overall_rating == 4.0
    assert rating.difficulty == 5


def test_a_restricted_hunter_cannot_file_a_game_flag(client):
    """The endpoint `can_interact` never covered: it had its own inline `is_linked` check, so it was
    the one UGC write a restriction would have sailed straight through."""
    hunter = _hunter()
    _restrict(hunter, 'reports')
    game = GameFactory()
    client.force_login(hunter.user)

    client.post(f'/api/v1/games/{game.pk}/flag/', {'flag_type': 'delisted'},
                content_type='application/json')

    assert GameFlag.objects.count() == 0, 'a restricted hunter filed a flag'


def test_a_restricted_hunter_cannot_report_a_quick_take(client):
    hunter, author = _hunter(), _hunter(psn='someoneelse')
    concept = ConceptFactory()
    rating = UserConceptRating.objects.create(
        profile=author, concept=concept, concept_trophy_group=None, blurb='their words',
        difficulty=5, grindiness=5, hours_to_platinum=20, fun_ranking=8, overall_rating=4.0)
    _restrict(hunter, 'reports')
    client.force_login(hunter.user)

    client.post(f'/api/v1/ratings/blurb/{rating.pk}/report/', {'reason': 'spam'},
                content_type='application/json')

    assert BlurbReport.objects.count() == 0


def test_a_quick_takes_restriction_does_not_stop_reporting(client):
    """Scopes are separate for a reason: somebody who writes abusive takes may still be a useful
    reporter, and over-reaching would make the narrow scope pointless."""
    hunter, author = _hunter(), _hunter(psn='someoneelse')
    concept = ConceptFactory()
    rating = UserConceptRating.objects.create(
        profile=author, concept=concept, concept_trophy_group=None, blurb='their words',
        difficulty=5, grindiness=5, hours_to_platinum=20, fun_ranking=8, overall_rating=4.0)
    _restrict(hunter, 'quick_takes')
    client.force_login(hunter.user)

    client.post(f'/api/v1/ratings/blurb/{rating.pk}/report/', {'reason': 'spam'},
                content_type='application/json')

    assert BlurbReport.objects.count() == 1, 'a quick-take restriction blocked reporting'


def test_a_full_restriction_blocks_every_way_of_writing(client):
    """THE test for this phase. Every UGC write endpoint, one restricted hunter, and the assertion is
    on the DATABASE -- a gate that redirects after writing passes a status-code test, and the whole
    failure mode here is silent.

    When a new UGC endpoint is added, it belongs in this sweep. That is the point of one test rather
    than five: there is a single place that answers "did we cover everything".
    """
    hunter, author = _hunter(), _hunter(psn='someoneelse')
    concept = ConceptFactory()
    their_rating = UserConceptRating.objects.create(
        profile=author, concept=concept, concept_trophy_group=None, blurb='their words',
        difficulty=5, grindiness=5, hours_to_platinum=20, fun_ranking=8, overall_rating=4.0)
    game = GameFactory()
    _restrict(hunter, 'all_ugc')
    client.force_login(hunter.user)

    _rate(client, hunter, blurb='a quick take')
    client.post(f'/api/v1/games/{game.pk}/flag/', {'flag_type': 'delisted'},
                content_type='application/json')
    client.post(f'/api/v1/ratings/blurb/{their_rating.pk}/report/', {'reason': 'spam'},
                content_type='application/json')

    assert GameFlag.objects.count() == 0, 'flagged a game'
    assert BlurbReport.objects.count() == 0, 'reported a take'
    assert not UserConceptRating.objects.filter(profile=hunter).exclude(blurb='').exists(), (
        'wrote a quick take')


def test_lifting_lets_them_write_again(client):
    """The other silent failure: a restriction that outlives its own lifting."""
    hunter = _hunter()
    restriction = _restrict(hunter, 'quick_takes')
    restriction_service.lift_restriction(restriction, _admin(), 'appealed, upheld')
    client.force_login(hunter.user)

    _rate(client, hunter, blurb='back again')

    assert UserConceptRating.objects.exclude(blurb='').count() == 1


def test_restricting_hides_nothing_already_published(client):
    """Say it in the UI and mean it in the code. An admin reaching for restrict when they meant hide
    is the likely mistake, and it must not silently do the other thing."""
    hunter = _hunter()
    concept = ConceptFactory()
    existing = UserConceptRating.objects.create(
        profile=hunter, concept=concept, concept_trophy_group=None, blurb='already published',
        difficulty=5, grindiness=5, hours_to_platinum=20, fun_ranking=8, overall_rating=4.0)

    _restrict(hunter, 'all_ugc')

    existing.refresh_from_db()
    assert existing.blurb_hidden is False, 'restricting hid what was already up'
    assert existing.blurb == 'already published'


# ── the admin surface ────────────────────────────────────────────────────────────────────────────

def test_a_moderator_cannot_restrict_anybody(client):
    """Deciding on a report is a moderator's. Barring somebody from writing is not."""
    hunter = _hunter()
    moderator = UserFactory()
    moderator.role = 'moderator'
    moderator.save()
    client.force_login(moderator)

    client.post(reverse('admin_restrict', args=[hunter.user.pk]),
                {'scope': 'all_ugc', 'reason': 'let me in'})

    assert UserRestriction.objects.count() == 0, 'a moderator restricted somebody'
    assert AdminAction.objects.count() == 0


def test_a_moderator_cannot_lift_one(client):
    hunter = _hunter()
    restriction = _restrict(hunter)
    moderator = UserFactory()
    moderator.role = 'moderator'
    moderator.save()
    client.force_login(moderator)

    client.post(reverse('admin_lift_restriction', args=[restriction.pk]), {'reason': 'let me in'})

    restriction.refresh_from_db()
    assert restriction.lifted_at is None, 'a moderator lifted a restriction'


def test_an_admin_restricts_from_the_persons_own_page(client):
    """The form lives on the person page because the decision needs what is above it: what they
    wrote, and what has already been decided about them. Restricting from a list of names is
    restricting without looking."""
    hunter = _hunter()
    client.force_login(_admin())

    client.post(reverse('admin_restrict', args=[hunter.user.pk]),
                {'scope': 'quick_takes', 'days': '7', 'reason': 'three slurs this week'})

    restriction = UserRestriction.objects.get()
    assert restriction.scope == 'quick_takes'
    assert restriction.expires_at is not None, 'a 7-day restriction was applied indefinitely'
    assert restriction_service.is_restricted_from(hunter, 'quick_takes') is True


def test_an_indefinite_restriction_is_what_an_empty_duration_means(client):
    hunter = _hunter()
    client.force_login(_admin())

    client.post(reverse('admin_restrict', args=[hunter.user.pk]),
                {'scope': 'all_ugc', 'days': '', 'reason': 'persistent abuse'})

    assert UserRestriction.objects.get().expires_at is None


def test_restricting_without_a_reason_changes_nothing(client):
    hunter = _hunter()
    client.force_login(_admin())

    client.post(reverse('admin_restrict', args=[hunter.user.pk]),
                {'scope': 'all_ugc', 'days': '', 'reason': '  '})

    assert UserRestriction.objects.count() == 0
    assert AdminAction.objects.count() == 0


def test_a_nonsense_duration_is_refused_rather_than_silently_indefinite(client):
    """The dangerous failure: swallowing it would turn a mistyped 7 into a permanent ban."""
    hunter = _hunter()
    client.force_login(_admin())

    client.post(reverse('admin_restrict', args=[hunter.user.pk]),
                {'scope': 'all_ugc', 'days': 'forever', 'reason': 'a real reason'})

    assert UserRestriction.objects.count() == 0


def test_the_restriction_list_shows_what_is_in_force(client):
    hunter = _hunter()
    _restrict(hunter, 'quick_takes')
    client.force_login(_admin())

    body = client.get(reverse('admin_restrictions')).content.decode()

    assert 'hunted47' in body
    assert 'spam, third time' in body, 'the reason is the part worth keeping'


def test_a_lifted_restriction_is_not_in_force_but_is_still_on_the_record(client):
    """Lapsed and lifted are different facts about the same account -- one served its time, one was
    cut short by a person with a reason -- and an admin judging a repeat needs to tell them apart."""
    hunter = _hunter()
    restriction = _restrict(hunter)
    restriction_service.lift_restriction(restriction, _admin(), 'appealed, upheld')
    client.force_login(_admin())

    live = client.get(reverse('admin_restrictions') + '?show=live').content.decode()
    ended = client.get(reverse('admin_restrictions') + '?show=ended').content.decode()

    assert 'hunted47' not in live
    assert 'hunted47' in ended
    assert 'appealed, upheld' in ended


def test_the_list_does_not_query_per_row(client):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client.force_login(_admin())
    _restrict(_hunter(psn='hunter0000'))

    with CaptureQueriesContext(connection) as few:
        client.get(reverse('admin_restrictions'))
    for n in range(6):
        _restrict(_hunter(psn=f'hunter{n:04d}x'))
    with CaptureQueriesContext(connection) as many:
        client.get(reverse('admin_restrictions'))

    assert len(many.captured_queries) <= len(few.captured_queries) + 2, (
        f'{len(few.captured_queries)} queries for 1 row, {len(many.captured_queries)} for 7')


def test_the_person_page_offers_the_restrict_form(client):
    hunter = _hunter()
    client.force_login(_admin())

    body = client.get(reverse('admin_person', args=[hunter.user.pk])).content.decode()

    assert reverse('admin_restrict', args=[hunter.user.pk]) in body
    assert 'Stops new words only' in body, (
        'the page does not say what a restriction does NOT do, which is the likely mistake')

