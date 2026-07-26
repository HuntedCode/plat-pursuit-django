"""Smoke + behavior tests for the populate_banned_words seed command."""
import pytest
from django.core.cache import cache
from django.core.management import call_command

from trophies.models import BannedWord
from trophies.services.comment_service import CommentService

pytestmark = pytest.mark.django_db


def test_populates_the_blocklist():
    call_command('populate_banned_words', verbosity=0)
    active = BannedWord.objects.filter(is_active=True)
    assert active.count() >= 100                          # a robust list, not a placeholder
    assert active.filter(word='fuck').exists()
    # Single tokens use word boundaries (no Scunthorpe); phrases match as substrings.
    assert BannedWord.objects.get(word='fuck').use_word_boundaries is True
    assert BannedWord.objects.get(word='click here').use_word_boundaries is False


def test_is_idempotent():
    call_command('populate_banned_words', verbosity=0)
    first = BannedWord.objects.count()
    call_command('populate_banned_words', verbosity=0)   # second run must not duplicate
    assert BannedWord.objects.count() == first


def test_clear_flag_resets():
    call_command('populate_banned_words', verbosity=0)
    BannedWord.objects.create(word='__stale__')
    call_command('populate_banned_words', '--clear', verbosity=0)
    assert not BannedWord.objects.filter(word='__stale__').exists()   # cleared first
    assert BannedWord.objects.filter(word='fuck').exists()            # then re-seeded


def test_dry_run_writes_nothing():
    call_command('populate_banned_words', '--dry-run', verbosity=0)
    assert BannedWord.objects.count() == 0


def test_racial_slur_is_boundary_matched_no_scunthorpe():
    """The slur is blocked, but legit words that merely contain it as a substring are not."""
    call_command('populate_banned_words', verbosity=0)
    cache.delete('banned_words:active')
    assert BannedWord.objects.get(word='nigger').use_word_boundaries is True     # boundary, not substring
    assert CommentService.check_banned_words('you nigger')[0] is True            # the slur itself: blocked
    assert CommentService.check_banned_words('what a snigger')[0] is False       # "snigger": allowed
    assert CommentService.check_banned_words('a niggardly reward')[0] is False   # "niggardly": allowed
