"""Milestones — the account-wide, cross-system "Hall of Records" recognition layer.

A milestone celebrates a long-horizon trophy-hunting-career feat that Badges and the gamification economy
don't already reward (e.g. lifetime platinums). It is **pure recognition**: tiered, but it grants no XP and
no Titles (those stay Badge rewards). See docs/design/milestones-revamp.md for the full design.

The split that keeps this extensible: a `Milestone` + its `MilestoneTier` rungs are DATA (author new ones
with no deploy); the ONLY code needed for a new *measurement* is a metric function (see `metrics.py`).

This is a fresh, self-contained app. The legacy `trophies.Milestone*` system is separate (dormant / retired
in a later phase); there is no relationship between the two.
"""
from django.db import models
from django.utils import timezone


class Milestone(models.Model):
    """A milestone concept (one row) — e.g. "Platinum Hunter". Its rungs live in `MilestoneTier`."""

    slug = models.SlugField(max_length=64, unique=True, help_text="Stable key (URL anchor, seed lookup).")
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, help_text="One line: what this celebrates.")
    icon = models.CharField(max_length=64, blank=True, help_text="Lucide-style icon key (template renders it).")
    # Key into milestones.metrics.MILESTONE_METRICS — the single whale-safe aggregate this milestone ladders on.
    metric = models.CharField(max_length=64)
    category = models.CharField(max_length=64, blank=True, help_text="Optional grouping bucket (unused at v1; flat list).")
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True,
                                    help_text="Uncheck to hide from the catalog. Earned history is preserved.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class MilestoneTier(models.Model):
    """One rung of a milestone ladder, as data. "10 / 50 / 100 platinums" = one Milestone + three tiers."""

    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name='tiers')
    index = models.PositiveIntegerField(help_text="1-based rung order (authority for ordering + 'highest earned').")
    threshold = models.PositiveIntegerField(help_text="Metric value required to earn this rung.")
    name = models.CharField(max_length=64, blank=True,
                            help_text="Optional flavour name (e.g. 'Legend'); blank = numeric display.")
    # Optional Discord role granted on earn (backend-only side-effect; never rendered). See services.reconcile.
    discord_role_id = models.BigIntegerField(null=True, blank=True,
                                             help_text="Discord role granted when this rung is reached (optional).")
    earned_count = models.PositiveIntegerField(default=0,
                                               help_text="Denormalized global earn counter -> rarity %. "
                                                         "F()-bumped on award, recomputed nightly.")

    class Meta:
        ordering = ['milestone', 'index']
        unique_together = [('milestone', 'index')]

    def __str__(self):
        return f"{self.milestone.name} · tier {self.index} ({self.threshold})"


class EarnedMilestoneTier(models.Model):
    """The permanent "you reached this rung" record. Never deleted — a career record."""

    profile = models.ForeignKey('trophies.Profile', on_delete=models.CASCADE,
                                related_name='earned_milestone_tiers')
    tier = models.ForeignKey(MilestoneTier, on_delete=models.CASCADE, related_name='earned_by')
    earned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [('profile', 'tier')]
        indexes = [models.Index(fields=['profile'], name='ms_earned_profile_idx')]

    def __str__(self):
        return f"{self.profile_id} · {self.tier_id}"


class UserMilestone(models.Model):
    """Materialized per-(profile, milestone) progress read-model. Written by the recompute sweep so the page
    reads it O(1) and never live-evaluates a metric on the request path."""

    profile = models.ForeignKey('trophies.Profile', on_delete=models.CASCADE,
                                related_name='milestone_progress')
    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name='user_progress')
    current_value = models.PositiveIntegerField(default=0, help_text="Last computed metric value.")
    highest_tier_index = models.PositiveIntegerField(default=0, help_text="Highest earned rung index (0 = none).")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('profile', 'milestone')]
        indexes = [models.Index(fields=['profile'], name='ms_progress_profile_idx')]

    def __str__(self):
        return f"{self.profile_id} · {self.milestone.slug} = {self.current_value}"
