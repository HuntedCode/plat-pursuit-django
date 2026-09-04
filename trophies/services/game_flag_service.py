import logging

from django.db import transaction
from django.utils import timezone

from trophies.models import GameFlag

logger = logging.getLogger(__name__)


class GameFlagService:
    """Handles community game flag submission and staff review logic."""

    VALID_FLAG_TYPES = [choice[0] for choice in GameFlag.FLAG_TYPES]

    #: flag_type -> (Game field, value) for the flags that map straight onto one boolean. Hoisted out
    #: of `approve_flag` so callers can ask WHAT a flag writes without running it -- the moderation
    #: log needs the field set to snapshot before/after, and deriving that by reading this function's
    #: source text (which is what it did first) breaks silently on a reformat.
    FIELD_ACTIONS = {
        'delisted':                ('is_delisted', True),
        'not_delisted':            ('is_delisted', False),
        'unobtainable':            ('is_obtainable', False),
        'obtainable':              ('is_obtainable', True),
        'has_online_trophies':     ('has_online_trophies', True),
        'no_online_trophies':      ('has_online_trophies', False),
        'has_buggy_trophies':      ('has_buggy_trophies', True),
        'buggy_trophies_resolved': ('has_buggy_trophies', False),
    }

    #: The shovelware flags do not fit the one-field shape -- they set a STATUS and a LOCK that
    #: overrides the automated classifier permanently.
    SHOVELWARE_FIELDS = ('shovelware_status', 'shovelware_lock')

    #: Every Game field an approval can write. THE contract for anything auditing a flag approval.
    #: Built from the map above rather than typed out again, so adding a flag type cannot forget it.
    WATCHED_FIELDS = sorted({f for f, _ in FIELD_ACTIONS.values()} | set(SHOVELWARE_FIELDS))

    @staticmethod
    @transaction.atomic
    def submit_flag(game, reporter, flag_type, details=''):
        """
        Submit a community flag for a game.

        If the reporter already has a pending flag of the same type on the
        same game, silently returns the existing flag. If a prior flag was
        approved or dismissed, a new one is created.

        Returns:
            tuple: (GameFlag, error_string_or_None)
        """
        if flag_type not in GameFlagService.VALID_FLAG_TYPES:
            return None, 'Invalid flag type.'

        existing = GameFlag.objects.filter(
            game=game, reporter=reporter, flag_type=flag_type, status='pending'
        ).first()
        if existing:
            return existing, None

        flag = GameFlag.objects.create(
            game=game,
            reporter=reporter,
            flag_type=flag_type,
            details=(details or '')[:500],
        )
        logger.info(
            'GameFlag created: type=%s game=%s reporter=%s',
            flag_type, game.pk, reporter.pk,
        )
        return flag, None

    @staticmethod
    @transaction.atomic
    def approve_flag(flag, reviewer):
        """Approve a flag and apply the corresponding Game field change."""
        game = flag.game
        update_fields = []

        actions = GameFlagService.FIELD_ACTIONS

        if flag.flag_type in actions:
            field, value = actions[flag.flag_type]
            setattr(game, field, value)
            update_fields.append(field)
        elif flag.flag_type == 'is_shovelware':
            game.shovelware_status = 'manually_flagged'
            game.shovelware_lock = True
            update_fields = ['shovelware_status', 'shovelware_lock']
        elif flag.flag_type == 'not_shovelware':
            game.shovelware_status = 'manually_cleared'
            game.shovelware_lock = True
            update_fields = ['shovelware_status', 'shovelware_lock']
        # missing_vr and region_incorrect: no automated change, staff handles manually

        if update_fields:
            game.save(update_fields=update_fields)

        flag.status = 'approved'
        flag.reviewed_at = timezone.now()
        flag.reviewed_by = reviewer
        flag.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])

        logger.info(
            'GameFlag approved: id=%s type=%s game=%s by=%s',
            flag.pk, flag.flag_type, game.pk, reviewer.pk,
        )

    @staticmethod
    def dismiss_flag(flag, reviewer):
        """Dismiss a flag without applying changes."""
        flag.status = 'dismissed'
        flag.reviewed_at = timezone.now()
        flag.reviewed_by = reviewer
        flag.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])

        logger.info(
            'GameFlag dismissed: id=%s type=%s game=%s by=%s',
            flag.pk, flag.flag_type, flag.game_id, reviewer.pk,
        )
