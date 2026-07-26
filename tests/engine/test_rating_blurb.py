"""Backend for the optional rating 'quick take' blurb (Phase 1).

Pins the form contract: the blurb is OPTIONAL (never gates a rating), sanitized (XSS-stripped),
banned-word filtered (shared with the comment blocklist), capped at 140 chars, and persists on the rating.
"""
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse

from trophies.forms import UserConceptRatingForm
from trophies.models import BannedWord, UserConceptRating
from tests.factories import ConceptFactory, ProfileFactory

pytestmark = pytest.mark.django_db

_VALID = {'difficulty': 6, 'grindiness': 4, 'hours_to_platinum': 30, 'fun_ranking': 8, 'overall_rating': 4.0}

# The rate endpoint gates on can_rate_group (linked + earned platinum). We patch that gate open so these
# tests focus on the blurb write-path logic, not the platinum-eligibility fixture.
_CAN_RATE = 'trophies.services.concept_trophy_group_service.ConceptTrophyGroupService.can_rate_group'


def _form(**over):
    return UserConceptRatingForm({**_VALID, **over})


def _rate(client, concept, **body):
    url = reverse('api:rating-group-rate', kwargs={'concept_id': concept.id, 'group_id': 'default'})
    return client.post(url, data=body, content_type='application/json', HTTP_CF_RAY='test')


def test_rating_valid_without_a_blurb():
    """The blurb must never be required -- a rating with no blurb submits exactly as before."""
    assert _form().is_valid()
    assert _form(blurb='').is_valid()
    f = _form(blurb='   ')
    assert f.is_valid() and f.cleaned_data['blurb'] == ''   # whitespace-only normalizes to empty


def test_blurb_is_trimmed():
    f = _form(blurb='  Great combat, brutal plat.  ')
    assert f.is_valid(), f.errors
    assert f.cleaned_data['blurb'] == 'Great combat, brutal plat.'


def test_blurb_strips_html():
    f = _form(blurb='<script>alert(1)</script>Fun one')
    assert f.is_valid()
    assert '<script>' not in f.cleaned_data['blurb']


def test_blurb_over_140_rejected():
    f = _form(blurb='x' * 141)
    assert not f.is_valid()
    assert 'blurb' in f.errors


def test_blurb_banned_word_rejected():
    BannedWord.objects.create(word='xyzzy', use_word_boundaries=True, is_active=True)
    cache.delete('banned_words:active')
    f = _form(blurb='what a xyzzy grind')
    assert not f.is_valid()
    assert 'blurb' in f.errors


def test_blurb_persists_on_the_rating():
    profile, concept = ProfileFactory(), ConceptFactory()
    f = _form(blurb='Solid platinum, do the MP first.')
    assert f.is_valid()
    rating = f.save(commit=False)
    rating.profile, rating.concept = profile, concept
    rating.save()
    assert UserConceptRating.objects.get(pk=rating.pk).blurb == 'Solid platinum, do the MP first.'
    assert UserConceptRating.objects.get(pk=rating.pk).blurb_hidden is False   # visible by default


def test_visible_blurbs_helper_excludes_empty_and_hidden():
    """The read helper is the one supported blurb query: present + not staff-hidden only."""
    concept = ConceptFactory()
    shown = UserConceptRating.objects.create(profile=ProfileFactory(), concept=concept, concept_trophy_group=None,
                                             blurb='Shown one.', **_VALID)
    UserConceptRating.objects.create(profile=ProfileFactory(), concept=concept, concept_trophy_group=None,
                                     blurb='', **_VALID)                                   # no blurb
    UserConceptRating.objects.create(profile=ProfileFactory(), concept=concept, concept_trophy_group=None,
                                     blurb='Hidden one.', blurb_hidden=True, **_VALID)     # staff-hidden
    visible = list(UserConceptRating.visible_blurbs().filter(concept=concept))
    assert visible == [shown]


# ── write path (the shared rate endpoint) ─────────────────────────────────────

@patch(_CAN_RATE, return_value=(True, None))
def test_numbers_only_update_keeps_existing_blurb(_can, client):
    """Regression: adjusting a rating without resending the blurb must NOT wipe the stored quick take."""
    profile, concept = ProfileFactory(is_linked=True, guidelines_agreed=True), ConceptFactory()
    client.force_login(profile.user)
    assert _rate(client, concept, **_VALID, blurb='Loved it, do MP first.').status_code == 200
    # A numbers-only update omits the blurb key entirely.
    resp = _rate(client, concept, difficulty=3, grindiness=2, hours_to_platinum=10, fun_ranking=9, overall_rating=5.0)
    assert resp.status_code == 200
    r = UserConceptRating.objects.get(profile=profile, concept=concept, concept_trophy_group=None)
    assert r.blurb == 'Loved it, do MP first.'   # survived
    assert r.difficulty == 3                       # numbers still applied


@patch(_CAN_RATE, return_value=(True, None))
def test_rate_response_echoes_stored_blurb(_can, client):
    """The response returns the stored (sanitized/trimmed) blurb so the client's live card matches on reload."""
    profile, concept = ProfileFactory(is_linked=True, guidelines_agreed=True), ConceptFactory()
    client.force_login(profile.user)
    resp = _rate(client, concept, **_VALID, blurb='  Tidy take.  ')
    assert resp.status_code == 200
    assert resp.json()['blurb'] == 'Tidy take.'   # trimmed stored value echoed, not the raw input


@patch(_CAN_RATE, return_value=(True, None))
def test_explicit_empty_blurb_clears_it(_can, client):
    """Explicitly sending blurb='' DOES clear it (the user removed their quick take)."""
    profile, concept = ProfileFactory(is_linked=True, guidelines_agreed=True), ConceptFactory()
    client.force_login(profile.user)
    _rate(client, concept, **_VALID, blurb='temporary')
    assert _rate(client, concept, **_VALID, blurb='').status_code == 200
    assert UserConceptRating.objects.get(profile=profile, concept=concept, concept_trophy_group=None).blurb == ''


@patch(_CAN_RATE, return_value=(True, None))
def test_blurb_requires_guidelines_agreement(_can, client):
    """A non-empty blurb needs guidelines agreement (parity with comments); numbers-only never does."""
    profile, concept = ProfileFactory(is_linked=True, guidelines_agreed=False), ConceptFactory()
    client.force_login(profile.user)
    assert _rate(client, concept, **_VALID).status_code == 200                    # numbers-only: fine
    resp = _rate(client, concept, **_VALID, blurb='Great game.')                  # blurb: blocked
    assert resp.status_code == 403 and resp.json().get('needs_guidelines') is True
    assert UserConceptRating.objects.get(profile=profile, concept=concept, concept_trophy_group=None).blurb == ''
