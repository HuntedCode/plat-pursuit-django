"""Badge Art Reveal event.

A time-boxed, community-driven event that reveals badge artwork as the whole
community earns platinum trophies on badge-covered games. Every
``platinums_per_reveal`` community platinums (counted from ``started_at``)
unlocks the next artwork in ``order``. On release, the uploaded art is pushed
onto the badge's ``badge_image`` so it goes live everywhere, permanently.

This is the donation-free sibling of the fundraiser's badge-artwork reveal:
the trigger is community platinum progress, not donations. The machinery here
is disposable (a temporary event), but the art it reveals is permanent.

The heavy community count runs ONLY in the cron (process_art_reveals), which
stores the result on ``last_platinum_count``; the banner and event page read
that cheap stored value and never recompute on the request path.
"""

from os.path import basename, splitext
from uuid import uuid4

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


def _artwork_upload_path(instance, filename):
    """Opaque, unguessable storage path for pre-release artwork. The UI never
    links a locked item's file, and the random name keeps the raw media URL (S3
    in prod) from being enumerated or guessed before the reveal."""
    return f"art_reveal/{uuid4().hex}{splitext(filename)[1].lower()}"


class ArtRevealEvent(models.Model):
    name = models.CharField(max_length=200, help_text='Display name shown on the event page and banner.')
    slug = models.SlugField(max_length=100, unique=True, help_text='URL identifier, e.g. "summer-2026".')
    description = models.TextField(blank=True, help_text='Optional intro shown on the event page.')

    is_active = models.BooleanField(
        default=False,
        help_text='Master switch. When off the event is hidden everywhere and the cron skips it.',
    )
    started_at = models.DateTimeField(help_text='Platinums earned from this moment onward count toward reveals.')
    ended_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Optional hard close. Leave blank to run until all artwork is revealed.',
    )

    platinums_per_reveal = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(1)],
        help_text='Community platinums needed to unlock each successive artwork.',
    )

    # Site-wide banner (mirrors fundraiser.Fundraiser).
    banner_active = models.BooleanField(
        default=False,
        help_text='Show the site-wide progress banner (only while the event is live).',
    )
    banner_text = models.CharField(
        max_length=200, blank=True,
        default='The community is revealing new badge artwork!',
        help_text='Message shown in the banner before the CTA.',
    )
    banner_dismiss_days = models.PositiveIntegerField(
        default=0,
        help_text='Days the banner stays dismissed after a user closes it. 0 means it reappears every session.',
    )

    # Cached counter, written by the cron only (never on the request path).
    last_platinum_count = models.PositiveIntegerField(
        default=0, help_text='Most recent community badge-platinum count (updated by the cron).',
    )
    last_counted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def is_live(self):
        """Active, started, and not past its optional end date."""
        now = timezone.now()
        if not self.is_active or now < self.started_at:
            return False
        if self.ended_at and now > self.ended_at:
            return False
        return True

    def show_banner(self):
        return self.banner_active and self.is_live()

    @property
    def total_items(self):
        return self.items.count()

    @property
    def released_count(self):
        return self.items.filter(released=True).count()

    def progress(self):
        """Banner/page progress, derived entirely from ``last_platinum_count`` so
        the displayed counts never disagree with the released-flag state (both are
        advanced together by the cron)."""
        per = self.platinums_per_reveal or 1
        total = self.total_items
        count = self.last_platinum_count
        revealed = min(count // per, total)
        complete = revealed >= total
        into_current = 0 if complete else count - revealed * per
        return {
            'count': count,
            'total': total,
            'revealed': revealed,
            'per_reveal': per,
            'complete': complete,
            'into_current': into_current,
            'remaining_to_next': 0 if complete else per - into_current,
            'next_threshold': None if complete else (revealed + 1) * per,
            'pct_next': 100 if complete else round(into_current / per * 100),
            'pct_overall': round(revealed / total * 100) if total else 0,
        }


class ArtRevealItem(models.Model):
    event = models.ForeignKey(ArtRevealEvent, on_delete=models.CASCADE, related_name='items')
    badge = models.ForeignKey(
        'trophies.Badge', on_delete=models.CASCADE, related_name='art_reveal_items',
        help_text='Tier-1 (base) badge of the series whose art is revealed. Tiers 2-4 inherit the art.',
    )
    order = models.PositiveIntegerField(help_text='Reveal position, 1-based. Lower numbers reveal first.')
    artwork = models.ImageField(
        upload_to=_artwork_upload_path,
        help_text='The to-be-revealed artwork. Hidden until released, then copied onto the badge.',
    )
    placeholder_label = models.CharField(
        max_length=120, blank=True,
        help_text='Optional teaser shown on the locked tile before release (defaults to a generic lock).',
    )

    released = models.BooleanField(default=False)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(fields=['event', 'order'], name='uniq_artreveal_event_order'),
            models.UniqueConstraint(fields=['event', 'badge'], name='uniq_artreveal_event_badge'),
        ]

    def __str__(self):
        return f"#{self.order} {self.badge.name} ({'released' if self.released else 'locked'})"

    @property
    def threshold(self):
        """Community platinum count at which this item unlocks."""
        return self.order * self.event.platinums_per_reveal

    def release(self, *, now=None):
        """Mark released and push the artwork onto the badge so it goes live
        everywhere. Idempotent: returns False if already released. The badge's
        existing art is never overwritten."""
        if self.released:
            return False
        now = now or timezone.now()
        if self.artwork and not self.badge.badge_image:
            with self.artwork.open('rb') as f:
                self.badge.badge_image.save(basename(self.artwork.name), f, save=True)
        self.released = True
        self.released_at = now
        self.save(update_fields=['released', 'released_at'])
        return True
