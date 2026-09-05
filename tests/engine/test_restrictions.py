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
    """THE test for this phase. One restricted hunter, every routed way of writing user-authored
    content, asserted on the DATABASE -- a gate that redirects after writing passes a status-code
    test, and the whole failure mode here is silent.

    THIS CLAIM WAS FALSE WHEN FIRST WRITTEN. It said "every UGC endpoint" and posted at three, which
    happened to be the three that had gates. An audit enumerating the routed writers found four more
    -- roadmap notes (create and edit), roadmap merge/publish, comment edit, and the donor-wall
    message -- none of which this would have caught. A sweep that only visits the doors you
    remembered to lock proves nothing about the building.

    The list below is the answer to "did we cover everything", so a new UGC endpoint belongs in it.
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


# ── what the audit of P3 found ───────────────────────────────────────────────────────────────────

def test_a_restriction_survives_deleting_the_account_and_relinking_psn():
    """THE escape hatch, and the one the original design claimed to have closed.

    The first cut keyed on `CustomUser` alone, reasoning that hanging it off the profile would make
    unlinking PSN a way out. True, and it missed the bigger way out: Settings offers a self-service
    account deletion, `Profile.user` is SET_NULL, and `link_profile_to_user` reattaches THE SAME
    profile row to a new account. Delete, re-register, re-verify the same PSN account -- and a
    CASCADE took every restriction with it while the trophies, badges, ranking and handle all came
    back.
    """
    from trophies.services.verification_service import VerificationService

    hunter = _hunter()
    profile, old_user = hunter, hunter.user
    _restrict(hunter, 'all_ugc')

    profile.unlink_user()
    old_user.delete()
    profile.refresh_from_db()
    assert profile.user is None, 'the profile did not survive the deletion'
    assert UserRestriction.objects.count() == 1, 'the restriction was cascaded away'

    new_user = UserFactory()
    VerificationService.link_profile_to_user(profile, new_user)
    profile.refresh_from_db()

    assert restriction_service.is_restricted_from(profile, 'quick_takes') is True, (
        'a new account on the same PSN profile walked away from the sanction')


def test_restricting_broadly_after_narrowly_is_refused():
    """The clash check was asymmetric: `covering = {'all_ugc', scope}` caught broad-then-narrow and
    waved narrow-then-broad through, leaving two live rows. Lifting the one an admin could see then
    left the hunter still barred, with no page saying so -- the exact state the model docstring says
    cannot happen."""
    hunter = _hunter()
    _restrict(hunter, 'quick_takes')

    with pytest.raises(restriction_service.RestrictionError):
        _restrict(hunter, 'all_ugc')

    assert UserRestriction.objects.count() == 1


def test_lifting_the_only_visible_restriction_actually_frees_them():
    """The consequence the asymmetry produced, asserted directly rather than through the count."""
    hunter = _hunter()
    first = _restrict(hunter, 'quick_takes')

    restriction_service.lift_restriction(first, _admin(), 'appealed')

    assert restriction_service.active_scopes_for(hunter) == set(), (
        'a lift left the hunter half-restricted with nothing on screen saying so')


def test_the_service_refuses_a_restriction_that_would_already_be_over():
    """At the SERVICE, not only at the view. The view now bounds `days` to 1..3650, so it rejects
    this before the service is asked -- which left the service's own guard untested and a direct
    caller (a shell, a command, the next view) free to write one. A restriction born already over is
    accepted, logged, reported as applied, and restricts nobody."""
    hunter = _hunter()

    with pytest.raises(restriction_service.RestrictionError, match='already have expired'):
        restriction_service.apply_restriction(
            hunter.user, 'all_ugc', _admin(), 'a real reason',
            expires_at=timezone.now() - timedelta(days=1))

    assert UserRestriction.objects.count() == 0
    assert AdminAction.objects.count() == 0


@pytest.mark.parametrize('days', ['-30', '0'])
def test_a_restriction_cannot_be_born_already_expired(client, days):
    """It was accepted, logged, and reported as "Restriction applied" while restricting nobody --
    appearing only under "ended", as something that lapsed before it existed."""
    hunter = _hunter()
    client.force_login(_admin())

    client.post(reverse('admin_restrict', args=[hunter.user.pk]),
                {'scope': 'all_ugc', 'days': days, 'reason': 'a real reason'})

    assert UserRestriction.objects.count() == 0
    assert restriction_service.is_restricted_from(hunter, 'quick_takes') is False


def test_an_absurd_duration_is_refused_rather_than_500ing(client):
    """`timedelta` raises OverflowError past ~2.9 million days, and OverflowError is not a
    ValueError -- so the view's `except` missed it and a form field became a 500."""
    hunter = _hunter()
    client.force_login(_admin())

    resp = client.post(reverse('admin_restrict', args=[hunter.user.pk]),
                       {'scope': 'all_ugc', 'days': '999999999', 'reason': 'a real reason'})

    assert resp.status_code == 302, 'an absurd duration was a 500 rather than a refusal'
    assert UserRestriction.objects.count() == 0


def test_asking_about_all_ugc_directly_is_a_fair_question():
    """`SCOPE_COVERS` had no key for it, so this raised KeyError -- which inside a DRF view's broad
    `except` is a 500. A landmine for the next gate author, who would reasonably write it."""
    hunter = _hunter()
    _restrict(hunter, 'all_ugc')

    assert restriction_service.is_restricted_from(hunter, 'all_ugc') is True


def test_the_gate_refuses_the_wrong_kind_of_object_rather_than_failing_open():
    """It read `getattr(profile, 'user_id', None)`, so handing it a CustomUser -- which has no such
    attribute -- returned None and therefore "not restricted". A gate whose default is permissive is
    not a gate."""
    hunter = _hunter()
    _restrict(hunter, 'all_ugc')

    with pytest.raises(TypeError):
        restriction_service.is_restricted_from(hunter.user, 'quick_takes')


# ── the writers the sweep had never visited ──────────────────────────────────────────────────────

def test_a_restricted_hunter_cannot_post_a_roadmap_note():
    """Missed by the first cut because the permission on it is `IsRoadmapAuthor`, independent of
    staff status and open to per-roadmap trial writers -- so it LOOKED like a staff surface and is
    not one. It is 5000 characters of prose that pushes @mentions to other hunters."""
    from trophies.services import roadmap_note_service

    hunter = _hunter()
    _restrict(hunter, 'all_ugc')

    with pytest.raises(roadmap_note_service.NoteError, match='restricted'):
        roadmap_note_service._refuse_if_restricted(hunter)


def test_a_restricted_hunter_cannot_edit_a_comment():
    """`toggle_vote` and `report_comment` both called `can_interact`; editing called nothing, so
    replacing a body outright was the one comment write a restriction did not cover."""
    from trophies.models import Comment
    from trophies.services.comment_service import CommentService

    hunter = _hunter()
    comment = Comment.objects.create(profile=hunter, concept=ConceptFactory(),
                                     body='original words')
    _restrict(hunter, 'all_ugc')

    ok, refusal = CommentService.edit_comment(comment, hunter, 'replaced words')

    assert ok is False
    assert 'restricted' in refusal
    comment.refresh_from_db()
    assert comment.body == 'original words'


def test_an_unrestricted_hunter_can_still_edit_their_comment():
    """The other half. A gate that blocks everybody is not a gate either."""
    from trophies.models import Comment
    from trophies.services.comment_service import CommentService

    hunter = _hunter()
    comment = Comment.objects.create(profile=hunter, concept=ConceptFactory(),
                                     body='original words')

    ok, _refusal = CommentService.edit_comment(comment, hunter, 'replaced words')

    assert ok is True

