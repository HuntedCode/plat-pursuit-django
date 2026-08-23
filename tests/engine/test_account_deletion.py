"""Account deletion (/users/settings/, action=delete_account) -- the 2026-08 settings review's
last addition, upgraded from a contact-us stub to a real flow.

The semantics under test are the ones locked in review: destroy the ACCOUNT, keep the public
pursuit. The PSN profile survives as an unlinked hunter with its numeric ratings intact
(community averages stay honest), the written quick takes are CLEARED (prose carries the
person's voice; a hidden state would be retention without a purpose), pending blurb reports
resolve as moot, and durable records (EmailLog, donations) detach via their SET_NULL FKs.
Cancel-first guard: an active or past-due membership blocks deletion; grace forfeits with a
warning. Stale processor webhooks for a deleted user must no-op (the handlers' DoesNotExist
guards are load-bearing now).
"""
import json
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse

from tests.factories import ConceptFactory, ProfileFactory
from trophies.models import BlurbReport, Profile, UserConceptRating
from users.models import CustomUser
from users.services.subscription_service import MembershipStatus, SubscriptionService

pytestmark = pytest.mark.django_db

URL = '/users/settings/'
PASSWORD = 'hunter-on-the-way-out-7'


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _account(client, with_blurb=True):
    profile = ProfileFactory(is_linked=True)
    user = profile.user
    user.set_password(PASSWORD)
    user.save()
    concept = ConceptFactory()
    rating = UserConceptRating.objects.create(
        profile=profile, concept=concept, difficulty=8, grindiness=9,
        hours_to_platinum=60, fun_ranking=7, overall_rating=4.5,
        recommendation='worth_it', blurb='A brutal, beautiful climb.' if with_blurb else '',
    )
    client.force_login(user)
    return profile, user, rating


def _delete(client, password=PASSWORD):
    return client.post(URL, {'action': 'delete_account', 'password': password})


# ── the semantics ─────────────────────────────────────────────────────────────────────────────

def test_deletion_destroys_the_account_and_keeps_the_public_pursuit(client):
    profile, user, rating = _account(client)
    user_pk, profile_pk = user.pk, profile.pk

    resp = _delete(client)

    assert resp.status_code == 302
    assert resp['Location'] == reverse('account_deleted')
    assert not CustomUser.objects.filter(pk=user_pk).exists()
    survivor = Profile.objects.get(pk=profile_pk)
    assert survivor.user is None
    assert survivor.is_linked is False


def test_scores_survive_and_blurbs_are_cleared(client):
    profile, user, rating = _account(client)

    _delete(client)

    rating.refresh_from_db()
    assert rating.difficulty == 8 and rating.overall_rating == 4.5, 'scores are statistical signal'
    assert rating.recommendation == 'worth_it'
    assert rating.blurb == '', 'prose carries the voice and must not outlive the account'


def test_pending_blurb_reports_resolve_as_moot_with_admin_bookkeeping(client):
    """Dismissed the way the admin's own action dismisses: a dismissed row with no reviewed_at
    reads as corruption in the moderation queue."""
    profile, user, rating = _account(client)
    reporter = ProfileFactory(is_linked=True)
    report = BlurbReport.objects.create(rating=rating, reporter=reporter, reason='spam')

    _delete(client)

    report.refresh_from_db()
    assert report.status == 'dismissed'
    assert report.reviewed_at is not None
    assert report.reviewed_by is None, 'system action, not a moderator'


def test_a_staff_hidden_blurb_clears_and_the_flag_resets(client):
    """The write path never resets blurb_hidden, so leaving it set would make a returning
    hunter's NEW quick take on the same game silently invisible."""
    profile, user, rating = _account(client)
    rating.blurb_hidden = True
    rating.save(update_fields=['blurb_hidden'])

    _delete(client)

    rating.refresh_from_db()
    assert rating.blurb == '' and rating.blurb_hidden is False


def test_deletion_is_atomic(client):
    """A failure at the last step rolls back EVERY irreversible step -- this is the test that
    would have caught the original unwrapped flow (half-destroyed account + 500)."""
    profile, user, rating = _account(client)
    reporter = ProfileFactory(is_linked=True)
    report = BlurbReport.objects.create(rating=rating, reporter=reporter, reason='spam')

    with patch.object(CustomUser, 'delete', side_effect=RuntimeError('mid-flow failure')):
        with pytest.raises(RuntimeError):
            _delete(client)

    rating.refresh_from_db()
    report.refresh_from_db()
    profile.refresh_from_db()
    assert rating.blurb != '', 'blurb clear rolled back'
    assert report.status == 'pending', 'report dismissal rolled back'
    assert profile.user_id == user.pk, 'unlink rolled back'


def test_staff_cannot_self_delete(client):
    """Refused before anything destructive runs: moderation history PROTECTs its moderator FK,
    so a staff self-delete would otherwise 500 mid-flow. Offboarding is by hand."""
    profile, user, rating = _account(client)
    user.is_staff = True
    user.save()
    client.force_login(user)

    resp = _delete(client)

    assert resp.status_code == 302 and resp['Location'] == URL
    assert CustomUser.objects.filter(pk=user.pk).exists()
    rating.refresh_from_db()
    assert rating.blurb != ''


def test_the_session_is_dead_after_deletion(client):
    _account(client)

    _delete(client)

    resp = client.get(URL)
    assert resp.status_code == 302 and 'login' in resp['Location']


def test_deletion_works_without_a_profile(client):
    profile, user, rating = _account(client)
    profile.unlink_user()

    resp = _delete(client)

    assert resp['Location'] == reverse('account_deleted')
    assert not CustomUser.objects.filter(pk=user.pk).exists()


# ── the guards ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('state', ['active', 'past_due'])
def test_a_live_membership_blocks_deletion(client, state):
    profile, user, rating = _account(client)

    with patch.object(SubscriptionService, 'membership_status',
                      return_value=MembershipStatus(state=state, provider='stripe')):
        resp = _delete(client)

    assert resp.status_code == 302 and resp['Location'] == URL
    assert CustomUser.objects.filter(pk=user.pk).exists(), 'cancel-first is server-enforced'


def test_a_paypal_id_with_no_scheduled_end_blocks_even_when_status_reads_none(client):
    """The belt-and-braces layer under membership_status: a live PayPal relationship a webhook
    hiccup left behind must not be orphaned by a deletion (the processor would keep charging
    with no site-side cancel path)."""
    profile, user, rating = _account(client)
    user.paypal_subscription_id = 'I-STRANDED123'
    user.save(update_fields=['paypal_subscription_id'])

    resp = _delete(client)

    assert resp['Location'] == URL
    assert CustomUser.objects.filter(pk=user.pk).exists()


def test_has_active_subscription_blocks_when_membership_status_misses(client):
    profile, user, rating = _account(client)

    with patch.object(SubscriptionService, 'has_active_subscription', return_value=(True, 'stripe')):
        resp = _delete(client)

    assert resp['Location'] == URL
    assert CustomUser.objects.filter(pk=user.pk).exists()


def test_grace_members_can_delete(client):
    profile, user, rating = _account(client)

    with patch.object(SubscriptionService, 'membership_status',
                      return_value=MembershipStatus(state='grace', provider='stripe')):
        resp = _delete(client)

    assert resp['Location'] == reverse('account_deleted')
    assert not CustomUser.objects.filter(pk=user.pk).exists()


def test_a_wrong_password_deletes_nothing(client):
    profile, user, rating = _account(client)

    resp = _delete(client, password='not-it')

    assert resp.status_code == 302 and resp['Location'] == URL
    assert CustomUser.objects.filter(pk=user.pk).exists()
    rating.refresh_from_db()
    assert rating.blurb != '', 'nothing is touched on a failed gate'


def test_the_password_gate_throttles_after_five_failures(client):
    profile, user, rating = _account(client)

    for _ in range(5):
        _delete(client, password='wrong')
    with patch.object(CustomUser, 'check_password') as check:
        _delete(client, password=PASSWORD)

    check.assert_not_called()
    assert CustomUser.objects.filter(pk=user.pk).exists()


def test_anonymous_visitors_cannot_reach_the_action(client):
    resp = client.post(URL, {'action': 'delete_account', 'password': 'x'})

    assert resp.status_code == 302 and 'login' in resp['Location']


# ── the dialog gating ─────────────────────────────────────────────────────────────────────────

def test_the_dialog_warns_about_blurbs_and_offers_the_download_only_when_there_are_any(client):
    profile, user, rating = _account(client, with_blurb=True)

    body = client.get(URL).content.decode()

    assert 'erased permanently' in body
    assert reverse('export_quick_takes') in body


def test_no_blurbs_means_a_shorter_dialog_with_no_download_link(client):
    profile, user, rating = _account(client, with_blurb=False)

    body = client.get(URL).content.decode()

    assert 'erased permanently' not in body
    assert reverse('export_quick_takes') not in body


def test_a_blocked_member_sees_the_cancel_first_row_instead_of_the_dialog(client):
    profile, user, rating = _account(client)

    with patch.object(SubscriptionService, 'membership_status',
                      return_value=MembershipStatus(state='active', provider='stripe')):
        body = client.get(URL).content.decode()

    assert 'Cancel it first' in body
    assert 'id="stg-delete"' not in body


# ── the export ────────────────────────────────────────────────────────────────────────────────

def test_the_export_carries_words_with_scores_and_skips_score_only_ratings(client):
    """Blurb-carrying rows only: that matches the "take your words with you" framing, and the
    score-only rows survive deletion anyway (nothing to save). Non-ASCII survives un-escaped
    (ensure_ascii=False + charset) and the DLC-group branch carries the group name."""
    from tests.factories import ConceptTrophyGroupFactory

    profile, user, rating = _account(client)
    rating.blurb = 'A brutal, beautiful climb. Ein Traum.'
    rating.save(update_fields=['blurb'])
    dlc_concept = ConceptFactory()
    ctg = ConceptTrophyGroupFactory(concept=dlc_concept, trophy_group_id='001',
                                    display_name='Frozen Wilds')
    UserConceptRating.objects.create(
        profile=profile, concept=dlc_concept, concept_trophy_group=ctg, difficulty=5,
        grindiness=5, hours_to_platinum=20, fun_ranking=8, overall_rating=4.0,
        blurb='Short and sharp.')
    score_only_concept = ConceptFactory()
    UserConceptRating.objects.create(
        profile=profile, concept=score_only_concept, difficulty=3, grindiness=2,
        hours_to_platinum=10, fun_ranking=6, overall_rating=3.5, blurb='')

    resp = client.get(reverse('export_quick_takes'))

    assert resp.status_code == 200
    assert 'attachment' in resp['Content-Disposition']
    payload = json.loads(resp.content)
    assert len(payload['ratings']) == 2, 'score-only ratings are not words'
    by_take = {r['quick_take']: r for r in payload['ratings']}
    assert by_take['A brutal, beautiful climb. Ein Traum.']['difficulty'] == 8
    assert by_take['Short and sharp.']['trophy_group'] == 'Frozen Wilds'
    assert 'Ein Traum' in resp.content.decode('utf-8'), 'non-ASCII ships un-escaped'


def test_the_delete_throttle_is_independent_of_the_password_change_throttle(client):
    profile, user, rating = _account(client)
    for _ in range(5):
        client.post(URL, {'action': 'change_password', 'old_password': 'wrong',
                          'new_password1': 'x-long-enough-99', 'new_password2': 'x-long-enough-99'})

    resp = _delete(client)

    assert resp['Location'] == reverse('account_deleted'), 'separate keys, separate budgets'


def test_the_export_requires_login(client):
    resp = client.get(reverse('export_quick_takes'))

    assert resp.status_code == 302 and 'login' in resp['Location']


# ── the aftermath ─────────────────────────────────────────────────────────────────────────────

def test_the_goodbye_page_renders_anonymously(client):
    body = client.get(reverse('account_deleted')).content.decode()

    assert 'Your account is deleted.' in body
    assert '{#' not in body


def test_a_stale_stripe_webhook_for_a_deleted_user_noops(client):
    """The handlers' CustomUser.DoesNotExist guards are load-bearing once accounts can die:
    a payment-failed event for a deleted customer must log and return, never raise."""
    SubscriptionService.handle_webhook_event(
        'invoice.payment_failed', {'customer': 'cus_deleted_long_ago'})
