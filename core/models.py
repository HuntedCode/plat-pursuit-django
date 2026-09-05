from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SiteEvent(models.Model):
    """
    Internal-only event tracking for admin analytics. Not shown publicly.
    Tracks guide visits, platinum share card downloads, recap interactions, and other site events.
    """
    EVENT_TYPE_CHOICES = [
        ('guide_visit', 'Guide Visit'),
        ('share_card_download', 'Platinum Share Card Download'),
        ('recap_page_view', 'Monthly Recap Page View'),
        ('recap_share_generate', 'Monthly Recap Share Card View'),
        ('recap_image_download', 'Recap Image Download'),
        ('game_list_create', 'Game List Created'),
        ('game_list_share', 'Game List Shared'),
        ('challenge_create', 'Challenge Created'),
        ('challenge_complete', 'Challenge Completed'),
        ('easter_egg', 'Easter Egg'),
        ('sync_search', 'Sync Search'),
        ('welcome_tour_complete', 'Welcome Tour Completed'),
        ('welcome_tour_skip', 'Welcome Tour Skipped'),
        ('game_detail_tour_complete', 'Game Detail Tour Completed'),
        ('game_detail_tour_skip', 'Game Detail Tour Skipped'),
        ('badge_detail_tour_complete', 'Badge Detail Tour Completed'),
        ('badge_detail_tour_skip', 'Badge Detail Tour Skipped'),
    ]

    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES, db_index=True)
    object_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="Related object ID: guide slug, earned_trophy_id, or recap 'YYYY-MM' string."
    )
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    user_id = models.IntegerField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['event_type', 'object_id'], name='se_type_object_idx'),
        ]
        ordering = ['-occurred_at']

    def __str__(self):
        return f"{self.event_type}:{self.object_id} at {self.occurred_at:%Y-%m-%d %H:%M}"


class EmailLog(models.Model):
    """
    Audit trail for all emails sent from the platform.

    Tracks subscription emails, account emails (verification, password reset),
    content emails (monthly recaps), and admin announcements. Used by the
    subscription admin dashboard and for general email audit purposes.
    """
    EMAIL_TYPES = [
        # Subscription lifecycle
        ('payment_failed', 'Payment Failed Warning'),
        ('payment_failed_final', 'Payment Failed Final Warning'),
        ('subscription_cancelled', 'Subscription Cancelled'),
        ('subscription_welcome', 'Subscription Welcome'),
        ('payment_succeeded', 'Payment Succeeded'),
        ('payment_action_required', 'Payment Action Required'),
        # Account
        ('email_verification', 'Email Verification'),
        ('password_reset', 'Password Reset'),
        # Content
        ('monthly_recap', 'Monthly Recap'),
        ('weekly_digest', 'Weekly Digest'),
        # Achievements
        ('badge_earned', 'Badge Earned'),
        ('milestone_achieved', 'Milestone Achieved'),
        # Onboarding
        ('welcome', 'Welcome'),
        # Admin
        ('admin_announcement', 'Admin Announcement'),
        ('launch_announcement', 'Launch Announcement'),
        # Fundraiser
        ('donation_receipt', 'Donation Receipt'),
        ('badge_claim_confirmation', 'Badge Claim Confirmation'),
        ('artwork_complete', 'Badge Artwork Complete'),
    ]
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('suppressed', 'Suppressed (Preference)'),
        ('failed', 'Failed'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='email_logs', null=True, blank=True,
    )
    recipient_email = models.EmailField()
    email_type = models.CharField(max_length=50, choices=EMAIL_TYPES, db_index=True)
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    triggered_by = models.CharField(
        max_length=30, default='system',
        help_text="Origin: system, webhook, admin_manual, management_command",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['email_type', '-created_at']),
        ]

    def __str__(self):
        target = self.user.email if self.user else self.recipient_email
        return f"{self.get_email_type_display()} -> {target} ({self.status})"


class CommunityTrophyDay(models.Model):
    """
    Daily aggregate of trophy activity from Discord-linked profiles.

    Computed and posted to Discord at ~12:30 PM ET for the previous ET day
    by the post_community_trophy_tracker management command. Each row is
    immutable after posting; the `posted_at` field gates against double-posts.

    Eligibility: Profile.discord_id IS NOT NULL, Trophy.game.shovelware_status='clean',
    EarnedTrophy.earned_date_time within the target ET calendar day.

    PP Score = total_trophies + (5 * total_platinums) + (3 * total_ultra_rares).
    Weights are applied at compute time and stored; changing the formula does
    NOT retroactively recompute historical rows.
    """
    date = models.DateField(unique=True, help_text="The ET calendar day these stats cover.")

    total_trophies = models.PositiveIntegerField(default=0)
    total_platinums = models.PositiveIntegerField(default=0)
    total_ultra_rares = models.PositiveIntegerField(default=0)
    pp_score = models.PositiveIntegerField(default=0)

    eligible_profile_count = models.PositiveIntegerField(
        default=0,
        help_text="Snapshot of how many Discord-linked profiles existed when this day was computed. Internal diagnostic.",
    )

    posted_at = models.DateTimeField(null=True, blank=True, help_text="When the Discord webhook post succeeded. Idempotency gate.")
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['-pp_score'], name='ctd_pp_score_idx'),
            models.Index(fields=['-total_platinums'], name='ctd_plat_idx'),
            models.Index(fields=['-total_trophies'], name='ctd_trophies_idx'),
            models.Index(fields=['-total_ultra_rares'], name='ctd_ur_idx'),
        ]
        verbose_name = "Community Trophy Day"
        verbose_name_plural = "Community Trophy Days"

    def __str__(self):
        return f"{self.date}: {self.total_trophies:,}T / {self.total_platinums:,}P / PP={self.pp_score:,}"


class AdminAction(models.Model):
    """Every act an admin performs on an account, a subscription or the site itself: who, why, and
    what it changed.

    The SECOND audit log, beside `trophies.ModerationAction`. Two tables on purpose:
    `ModerationAction` carries real FKs to `BlurbReport` and `GameFlag`, which a billing entry has
    nothing to put in, and a moderation entry has nothing to say about a Stripe subscription id.
    Bending one table around both subjects is how a column ends up meaning two things.

    They share their RULES, not their schema. See `core/services/audit.py`, and
    `tests/engine/test_admin_audit.py`, which holds the two in step by introspection.

    Lives in `core` because it spans users (subscriptions), fundraiser (badge claims) and trophies
    (badge series), and `core` already owns the cross-app record tables (`SiteEvent`, `EmailLog`).

    NO GenericForeignKey -- and there is not one anywhere in this codebase. A GFK stores a
    content-type id with no label, so once the target row is gone the entry reads as "somebody did
    something to nothing", which is exactly the failure the frozen-label design exists to prevent.
    The `target_type` / `target_id` / `target_label` triple identifies a target BETTER after it is
    deleted, `target_id` holds a Stripe id or a slug as happily as a primary key, and `subject_user`
    answers "everything ever done to this person" with a single index -- which a GFK cannot do at all.
    """
    ACTIONS = [
        ('restriction_applied', 'Restriction applied'),
        ('restriction_lifted', 'Restriction lifted'),
        ('subscription_force_deactivated', 'Subscription force-deactivated'),
        ('subscription_email_resent', 'Subscription email resent'),
        ('claim_status_changed', 'Badge claim status changed'),
    ]

    #: What KIND of thing was acted on. A type plus an id rather than a content-type FK: this
    #: survives the target's deletion, and reading it needs no join.
    TARGET_TYPES = [
        ('user', 'User'),
        ('subscription', 'Subscription'),
        ('restriction', 'Restriction'),
        ('badge_claim', 'Badge claim'),
        ('fundraiser', 'Fundraiser'),
        ('system', 'System'),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, db_index=False,
        related_name='admin_actions',
        help_text='The admin who acted. SET_NULL so deleting a staff account never erases what they '
                  'did -- `actor_label` keeps the name.',
    )
    actor_label = models.CharField(
        max_length=150, blank=True,
        help_text="The actor's name at the time, so the entry still reads once the account is gone.",
    )
    action = models.CharField(max_length=40, choices=ACTIONS)
    reason = models.TextField(
        help_text='REQUIRED, and required by the service rather than by a form the next caller might '
                  'not use -- a management command, a shell, or a Django-admin bulk action that '
                  'renders no form at all.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, db_index=False,
        related_name='admin_actions_received',
        help_text='The person this was done TO, as distinct from the thing that was changed. This is '
                  'what makes "everything ever done to this account" a single indexed query. Null '
                  'for system actions with nobody on the other end.',
    )
    subject_label = models.CharField(
        max_length=150, blank=True,
        help_text="The subject's name at the time. Same reason as `actor_label`.",
    )

    target_type = models.CharField(max_length=32, choices=TARGET_TYPES, blank=True)
    target_id = models.CharField(
        # 255, matching the WIDEST thing it must hold rather than a round number that felt roomy.
        # Measured: djstripe's StripeIdField and `CustomUser.stripe_customer_id` are 255, a
        # badge-series slug is 100. At 64 a series slug could not physically be written -- and since
        # Django does not truncate a CharField on save, that reaches Postgres unclipped, raises, and
        # aborts the enclosing atomic block, rolling back the very change the entry was recording.
        # Exactly the failure `changed`/`evidence` use DjangoJSONEncoder to avoid, one field up.
        max_length=255, blank=True,
        help_text='Identifier of the thing acted on, as TEXT. Not an integer: a Stripe subscription '
                  'id, a badge-series slug and a primary key all have to fit in this one column, and '
                  'a log that can only reference integer PKs cannot describe half the actions here.',
    )
    target_label = models.CharField(
        max_length=255, blank=True,
        help_text='Human-readable identification of what was acted on, captured at the time.',
    )

    changed = models.JSONField(
        default=dict, blank=True, encoder=DjangoJSONEncoder,
        help_text="{field: [before, after]} for whatever the action actually WROTE -- read from the "
                  "object, never assumed. An empty dict is a real outcome, not a bug. "
                  "DjangoJSONEncoder so a datetime or Decimal cannot raise mid-transaction.",
    )
    evidence = models.JSONField(
        default=dict, blank=True, encoder=DjangoJSONEncoder,
        help_text="What the action was ABOUT but did not change. Kept beside the diff rather than "
                  "inside it, so a diff view cannot render it as a field that was modified.",
    )

    reverses = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reversed_by_action',
        help_text='Set when this entry UNDOES an earlier one -- lifting a restriction, most of all. '
                  'A reversal is its own entry rather than an edit to the original: an audit trail '
                  'that can be rewritten is not one.',
    )

    class Meta:
        # `-id` breaks ties: created_at is auto_now_add, so a bulk write can produce identical
        # timestamps and leave paging through the log non-deterministic across page boundaries.
        ordering = ['-created_at', '-id']
        # Every index carries `-id`, for the same reason `ordering` does: stopping at `-created_at`
        # leaves `ORDER BY created_at DESC, id DESC` needing a sort on top of the index instead of
        # reading straight off it.
        #
        # `db_index=False` on both user FKs above: Django's automatic single-column FK index is a
        # strict PREFIX of these composites and can answer nothing they cannot. On an append-only log
        # that is two indexes of write cost for no read at all.
        indexes = [
            models.Index(fields=['-created_at', '-id'], name='adminaction_recent_idx'),
            models.Index(fields=['actor', '-created_at', '-id'], name='adminaction_actor_idx'),
            models.Index(fields=['subject_user', '-created_at', '-id'],
                         name='adminaction_subject_idx'),
            models.Index(fields=['action', '-created_at', '-id'], name='adminaction_action_idx'),
        ]
        constraints = [
            # One reversal per action, enforced by the DATABASE. The service takes a row lock; this
            # is what holds if anything ever writes without one.
            models.UniqueConstraint(
                fields=['reverses'], condition=Q(reverses__isnull=False),
                name='adminaction_one_reversal_per_action',
            ),
            # The same treatment for the reason, which had the same rhetoric and none of the
            # enforcement. `blank=False` is a forms attribute with no DDL behind it, and Postgres
            # NOT NULL does not exclude the empty string -- so `create(reason='')` succeeded while
            # the help_text called a reason REQUIRED. The DEPTH of a reason stays a service rule
            # (three characters, stripped); this is the floor beneath it.
            models.CheckConstraint(
                condition=Q(reason__regex=r'\S'),
                name='adminaction_reason_is_not_blank',
            ),
        ]

    def __str__(self):
        who = self.actor_label or 'deleted user'
        return f"{self.get_action_display()} by {who} on {self.target_label or 'unknown target'}"

    @property
    def is_reversed(self):
        """Derived, not stored: a flag would be a second copy of a fact the FK already carries, and
        the two would eventually disagree."""
        return self.reversed_by_action.exists()
