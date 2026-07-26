"""Backend for the optional rating 'quick take' blurb (Phase 1).

Pins the form contract: the blurb is OPTIONAL (never gates a rating), sanitized (XSS-stripped),
banned-word filtered (shared with the comment blocklist), capped at 140 chars, and persists on the rating.
"""
import pytest
from django.core.cache import cache

from trophies.forms import UserConceptRatingForm
from trophies.models import BannedWord, UserConceptRating
from tests.factories import ConceptFactory, ProfileFactory

pytestmark = pytest.mark.django_db

_VALID = {'difficulty': 6, 'grindiness': 4, 'hours_to_platinum': 30, 'fun_ranking': 8, 'overall_rating': 4.0}


def _form(**over):
    return UserConceptRatingForm({**_VALID, **over})


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
