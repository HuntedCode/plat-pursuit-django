"""Send a one-time in-app notification about the review-system retirement.

Reviews were archived in 2026-05 after a data-handling bug during the
IGDB-anchoring migration cascade-deleted some reviews (and some ratings).
The lost rows are unrecoverable and there's NO audit trail of who lost
what, so we can't target affected users precisely. This command sends a
best-effort heads-up to the most-engaged reviewers via two durable
signals that survive the deleted Review rows:

  1. Profiles that still have at least one (non-deleted) Review.
  2. Profiles that earned a review milestone (UserMilestone with
     criteria_type in review_count / review_helpful_count) — proof they
     wrote reviews even if those reviews are now gone.

The unreachable tail (low-volume authors who lost everything and never
hit a milestone) is covered by the broader channels: the notice page at
the old review URLs and the Discord announcement.

DEFAULTS TO DRY-RUN. Pass --send to actually create notifications.
Idempotent guard: skips users who already have a review_archival
notification (so re-running --send doesn't double-notify).
"""
from django.core.management.base import BaseCommand
from django.urls import reverse

REVIEW_MILESTONE_CRITERIA = ['review_count', 'review_helpful_count']

NOTIF_TITLE = "Reviews are taking a break"
NOTIF_MESSAGE = (
    "We've temporarily retired community reviews. Some review and rating "
    "data was lost in the process, and we're sorry. Reviews will return, "
    "rebuilt to be more reliable."
)
NOTIF_DETAIL = (
    "During a behind-the-scenes upgrade to how we match games to their "
    "IGDB entries, a data-handling bug caused some reviews to be lost. The "
    "same bug touched a small number of game ratings too.\n\n"
    "Rather than carry on with a system we no longer fully trust, we're "
    "putting reviews away for now and will rebuild them properly. If a "
    "rating of yours is missing, you can re-add it any time through Rate My "
    "Games. Thanks for sticking with us."
)


class Command(BaseCommand):
    help = (
        "Send a one-time in-app notification about the review retirement to "
        "the best-effort audience (review survivors + review-milestone "
        "earners). Dry-run by default; pass --send to deliver."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--send', action='store_true',
            help='Actually create the notifications. Without this it dry-runs.',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Cap the number of recipients (testing).',
        )

    def handle(self, *args, **options):
        send = options['send']
        limit = options['limit']

        from django.contrib.auth import get_user_model
        from trophies.models import Review, UserMilestone
        from notifications.models import Notification

        User = get_user_model()

        # Durable-signal audience (profile ids).
        review_profile_ids = set(
            Review.objects.filter(is_deleted=False)
            .values_list('profile_id', flat=True).distinct()
        )
        milestone_profile_ids = set(
            UserMilestone.objects.filter(
                milestone__criteria_type__in=REVIEW_MILESTONE_CRITERIA,
            ).values_list('profile_id', flat=True).distinct()
        )
        audience_profile_ids = review_profile_ids | milestone_profile_ids
        audience_profile_ids.discard(None)

        self.stdout.write(
            f'Audience: {len(review_profile_ids)} with surviving reviews + '
            f'{len(milestone_profile_ids)} review-milestone earners = '
            f'{len(audience_profile_ids)} unique profiles.'
        )

        users = (
            User.objects.filter(profile__id__in=audience_profile_ids)
            .distinct()
        )

        # Idempotency: skip users already notified.
        already = set(
            Notification.objects.filter(
                notification_type='admin_announcement',
                metadata__campaign='review_archival',
            ).values_list('recipient_id', flat=True)
        )
        if already:
            users = users.exclude(id__in=already)
            self.stdout.write(
                f'Skipping {len(already)} user(s) already notified.'
            )

        if limit:
            users = users[:limit]

        total = users.count()
        action_url = reverse('reviews_landing')

        if not send:
            self.stdout.write(self.style.WARNING(
                f'\n[DRY RUN] Would notify {total} user(s). Re-run with --send '
                f'to deliver.\nTitle: {NOTIF_TITLE!r}\nAction: {action_url}'
            ))
            for u in users[:10]:
                self.stdout.write(f'  - {u.username} (id={u.id})')
            if total > 10:
                self.stdout.write(f'  ... and {total - 10} more')
            return

        from notifications.services.notification_service import NotificationService

        created = NotificationService.send_bulk_notification(
            recipients_queryset=users,
            notification_type='admin_announcement',
            title=NOTIF_TITLE,
            message=NOTIF_MESSAGE,
            detail=NOTIF_DETAIL,
            icon='📝',
            action_url=action_url,
            action_text='Learn more',
            priority='normal',
            metadata={'campaign': 'review_archival'},
        )
        self.stdout.write(self.style.SUCCESS(
            f'Delivered {created} review-archival notification(s).'
        ))
