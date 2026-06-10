"""Tests for the backfill_earn_ranks command (ranks unranked badge earners)."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import UserBadge
from tests.factories import BadgeFactory, ProfileFactory, UserBadgeFactory

pytestmark = pytest.mark.django_db


def test_backfill_ranks_earners_by_earned_at():
    badge = BadgeFactory(tier=1)
    now = timezone.now()
    ubs = []
    for i in range(3):
        ub = UserBadgeFactory(badge=badge, profile=ProfileFactory())
        # earned_at is auto_now_add; set distinct times (oldest first) + clear rank.
        UserBadge.objects.filter(pk=ub.pk).update(
            earn_rank=None, earned_at=now - timedelta(days=3 - i),
        )
        ubs.append(ub)

    call_command('backfill_earn_ranks')

    assert UserBadge.objects.get(pk=ubs[0].pk).earn_rank == 1  # earliest earned
    assert UserBadge.objects.get(pk=ubs[1].pk).earn_rank == 2
    assert UserBadge.objects.get(pk=ubs[2].pk).earn_rank == 3

    # idempotent
    call_command('backfill_earn_ranks')
    assert UserBadge.objects.get(pk=ubs[2].pk).earn_rank == 3


def test_backfill_leaves_already_ranked_badges_untouched():
    badge = BadgeFactory(tier=1)
    ub = UserBadgeFactory(badge=badge, profile=ProfileFactory())
    UserBadge.objects.filter(pk=ub.pk).update(earn_rank=5)

    call_command('backfill_earn_ranks')

    assert UserBadge.objects.get(pk=ub.pk).earn_rank == 5  # no NULLs -> skipped
