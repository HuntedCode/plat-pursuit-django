import logging

from django.db import transaction
from django.utils import timezone

from trophies.models import GameFlag

logger = logging.getLogger(__name__)


class GameFlagSubmissionError(Exception):
    """A bulk submission was refused mid-loop. Raised rather than returned so `submit_flags`'
    `transaction.atomic` rolls the partial batch back -- a returned error would leave the rows
    filed before the refusal committed."""


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
    SHOVELWARE_FLAG_TYPES = ('is_shovelware', 'not_shovelware')
    SHOVELWARE_FIELDS = ('shovelware_status', 'shovelware_lock')

    #: Flag types an approval writes NOTHING for. Approving one means "confirmed, a human should act".
    #:
    #: DERIVED from the two maps above rather than listed, because the list was the bug: the queue
    #: template and two code comments each hand-named `missing_vr` and `region_incorrect` and each
    #: missed `other`, so an `other` row told a moderator "approving updates this game's flags
    #: directly" for a flag that updates nothing. A thirteenth flag type added tomorrow joins this
    #: set automatically unless it is given a field to write.
    NO_OP_FLAG_TYPES = sorted(
        set(VALID_FLAG_TYPES) - set(FIELD_ACTIONS) - set(SHOVELWARE_FLAG_TYPES))

    #: Every Game field an approval can write. THE contract for anything auditing a flag approval.
    #: Built from the map above rather than typed out again, so adding a flag type cannot forget it.
    WATCHED_FIELDS = sorted({f for f, _ in FIELD_ACTIONS.values()} | set(SHOVELWARE_FIELDS))

    #: Most versions any one bulk submission may carry. Sized well above a real trophy-list set
    #: (the widest concepts on the site run ~8-10 lists across platforms and regions) and well
    #: below "a script found the endpoint". The per-user rate limit counts REQUESTS, so without a
    #: per-request cap one request could file arbitrarily many rows.
    MAX_BULK_VERSIONS = 25

    #: Shared with the API layer, which re-checks both BEFORE doing the peer query so an oversized
    #: or empty payload is refused without touching the DB. One definition so the two cannot drift
    #: into telling the reporter two different things.
    ERR_NO_VERSIONS = 'Select at least one version to report.'
    ERR_TOO_MANY = f'You can report at most {MAX_BULK_VERSIONS} versions at once.'

    @staticmethod
    def _clean_details(details):
        """Coerce to a string, THEN cap at 500.

        `(details or '')[:500]` assumed a string. Slicing a list is a no-op that returns the list,
        and `TextField.to_python` then `str()`s it on save -- so `{"details": ["A" * 20000]}` stored
        20 KB per row, past a cap that looked like it was enforcing 500. A dict was worse: slicing
        one raises TypeError inside the transaction, surfacing as a 500 on what should be a 400.
        `max_length=500` is a form-level hint on a TextField, not a DB constraint, so this slice is
        the only thing standing between a payload and the column.
        """
        if details is None:
            return ''
        if not isinstance(details, str):
            details = str(details)
        return details[:500]

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

        # Also in the SERVICE, not only at the view. This is the single writer of GameFlag rows, so
        # a second caller -- a management command, a shell, a future endpoint -- cannot route around
        # a restriction by not knowing about it.
        from users.services import restriction_service

        if restriction_service.is_restricted_from(reporter, 'reports'):
            return None, 'Your account is currently restricted from filing reports.'

        existing = GameFlag.objects.filter(
            game=game, reporter=reporter, flag_type=flag_type, status='pending'
        ).first()
        if existing:
            return existing, None

        flag = GameFlag.objects.create(
            game=game,
            reporter=reporter,
            flag_type=flag_type,
            details=GameFlagService._clean_details(details),
        )
        logger.info(
            'GameFlag created: type=%s game=%s reporter=%s',
            flag_type, game.pk, reporter.pk,
        )
        return flag, None

    @staticmethod
    @transaction.atomic
    def submit_flags(games, reporter, flag_type, details=''):
        """Submit the SAME flag against several versions of one game. One transaction.

        Returns ``(created_count, duplicate_count, error_string_or_None)``.

        `duplicate_count` is versions the reporter already had a pending flag of this type on.
        `submit_flag` returns those silently -- correct for it, wrong to report as new work here,
        because "Reported 5 versions" for a submission that filed nothing is the one message this
        screen can send that is actually false. The count comes from a single pre-query over the
        set rather than from `submit_flag`'s return, which cannot distinguish the two cases without
        a signature change that would ripple to its other caller.

        COST: 1 + 3N queries (N <= MAX_BULK_VERSIONS), because `submit_flag` re-runs the restriction
        check and an existence lookup per row. Both are hoistable and deliberately not hoisted:
        `submit_flag` is the single writer of GameFlag rows AND the place the reporting restriction
        is enforced, so a loop that skipped past it to save queries would be a second writer with
        the restriction optional. Bounded at 25 on a rare, deliberate user action, this is the
        cheaper side of that trade -- it is NOT the profile-scoped aggregation the whale rule is
        about, and must not grow into one.
        """
        # Dedup by pk BEFORE counting anything. `created` counts list elements not already pending,
        # so the same Game passed twice reported 2 created against 1 row written -- the API builds a
        # set and never hits it, but this is a public service method with its own contract to keep.
        games = list({g.pk: g for g in games}.values())
        if not games:
            return 0, 0, GameFlagService.ERR_NO_VERSIONS
        if len(games) > GameFlagService.MAX_BULK_VERSIONS:
            return 0, 0, GameFlagService.ERR_TOO_MANY
        if flag_type not in GameFlagService.VALID_FLAG_TYPES:
            return 0, 0, 'Invalid flag type.'

        already = set(
            GameFlag.objects.filter(
                game__in=games, reporter=reporter, flag_type=flag_type, status='pending',
            ).values_list('game_id', flat=True)
        )

        created = 0
        for game in games:
            # Through `submit_flag`, never straight to `objects.create`: it is the single writer of
            # GameFlag rows and it is where the reporting restriction is enforced. A loop that
            # inlined the create would be a second writer that routes around the restriction.
            _flag, error = GameFlagService.submit_flag(game, reporter, flag_type, details)
            if error:
                # Atomic: the first refusal (a restriction, most likely) rolls the whole batch back
                # rather than filing a partial set the reporter cannot see or amend.
                raise GameFlagSubmissionError(error)
            if game.pk not in already:
                created += 1

        logger.info(
            'GameFlags created in bulk: type=%s reporter=%s created=%s duplicate=%s',
            flag_type, reporter.pk, created, len(already),
        )
        return created, len(already), None

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
        # Everything in NO_OP_FLAG_TYPES (missing_vr, region_incorrect, other) falls through here:
        # no automated change, a human follows up. Do not hand-list them again anywhere.

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
