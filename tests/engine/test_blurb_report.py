"""BlurbReport: the reactive-moderation report endpoint + the absorb-safety invariant.

The report FKs the rating (not Concept), so a concept merge never orphans it: it cascades when a rating is
deduped away and stays attached when a rating is re-pointed. These pin both the endpoint and that invariant.
"""
import pytest
from django.urls import reverse

from trophies.models import BlurbReport, UserConceptRating
from tests.factories import ConceptFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _rating(concept=None, profile=None, blurb='Great combat, brutal plat.', hidden=False):
    return UserConceptRating.objects.create(
        profile=profile or ProfileFactory(), concept=concept or ConceptFactory(), concept_trophy_group=None,
        difficulty=6, grindiness=4, hours_to_platinum=30, fun_ranking=8, overall_rating=4.0,
        blurb=blurb, blurb_hidden=hidden,
    )


def _url(rating):
    return reverse('api:rating-blurb-report', kwargs={'rating_id': rating.id})


def _post(client, rating, **body):
    return client.post(_url(rating), data=body, content_type='application/json', HTTP_CF_RAY='test')


# ── endpoint ────────────────────────────────────────────────────────────────

def test_report_a_blurb(client):
    rating = _rating()
    reporter = ProfileFactory(is_linked=True)
    client.force_login(reporter.user)
    resp = _post(client, rating, reason='inappropriate', details='not ok')
    assert resp.status_code == 200 and resp.json()['success'] is True
    assert BlurbReport.objects.filter(rating=rating, reporter=reporter, reason='inappropriate').exists()


def test_report_is_deduped_per_user(client):
    rating = _rating()
    reporter = ProfileFactory(is_linked=True)
    client.force_login(reporter.user)
    _post(client, rating, reason='spam')
    _post(client, rating, reason='spam')
    assert BlurbReport.objects.filter(rating=rating, reporter=reporter).count() == 1


def test_cannot_report_own_blurb(client):
    owner = ProfileFactory(is_linked=True)
    rating = _rating(profile=owner)
    client.force_login(owner.user)
    assert _post(client, rating, reason='spam').status_code == 400


def test_no_visible_blurb_is_404(client):
    reporter = ProfileFactory(is_linked=True)
    client.force_login(reporter.user)
    assert _post(client, _rating(blurb=''), reason='spam').status_code == 404          # no blurb
    assert _post(client, _rating(hidden=True), reason='spam').status_code == 404        # already hidden


def test_report_requires_linked_profile(client):
    rating = _rating()
    client.force_login(ProfileFactory(is_linked=False).user)
    assert _post(client, rating, reason='spam').status_code == 403


def test_unknown_reason_falls_back_to_other(client):
    rating = _rating()
    reporter = ProfileFactory(is_linked=True)
    client.force_login(reporter.user)
    _post(client, rating, reason='not-a-real-reason')
    assert BlurbReport.objects.get(rating=rating, reporter=reporter).reason == 'other'


# ── absorb-safety invariant (report follows / cascades with its rating) ───────

def test_report_cascades_when_rating_is_deleted():
    rating = _rating()
    BlurbReport.objects.create(rating=rating, reporter=ProfileFactory(), reason='spam')
    rid = rating.id
    rating.delete()
    assert not BlurbReport.objects.filter(rating_id=rid).exists()


def test_blurb_and_report_follow_a_repointed_rating():
    """A base-game rating re-points its concept during absorb; its blurb + report ride along (same row)."""
    survivor = ConceptFactory()
    rating = _rating()
    report = BlurbReport.objects.create(rating=rating, reporter=ProfileFactory(), reason='spam')
    rating.concept = survivor
    rating.save()
    rating.refresh_from_db()
    assert rating.concept_id == survivor.id and rating.blurb                       # blurb intact
    assert BlurbReport.objects.get(id=report.id).rating_id == rating.id            # report still attached
