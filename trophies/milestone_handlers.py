from django.core.exceptions import ObjectDoesNotExist
from trophies.models import ProfileGame
from trophies.milestone_constants import MONTH_MAP

MILESTONE_HANDLERS = {}

def register_handler(criteria_type):
    """Decorator to register handlers."""
    def decorator(func):
        MILESTONE_HANDLERS[criteria_type] = func
        return func
    return decorator

@register_handler('manual')
def handle_manual(profile, milestone, _cache=None):
    """Always return not achieved unless already awarded."""
    try:
        user_milestone = profile.user_milestones.get(milestone=milestone)
        return {'achieved': True, 'progress': milestone.required_value}
    except ObjectDoesNotExist:
        return {'achieved': False, 'progress': 0}

@register_handler('plat_count')
def handle_plat_count(profile, milestone, _cache=None):
    """Check progress for plat_count"""
    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'plat_count' in _cache:
        current = _cache['plat_count']
    else:
        current = ProfileGame.objects.filter(profile=profile, has_plat=True).count()
        if _cache is not None:
            _cache['plat_count'] = current
    achieved = current >= target
    return {'achieved': achieved, 'progress': current}

@register_handler('psn_linked')
def handle_psn_linked(profile, milestone, _cache=None):
    """Check if PSN profile is linked to a user account (Profile.is_linked)"""
    achieved = profile.is_linked
    progress = 1 if achieved else 0
    return {'achieved': achieved, 'progress': progress}

@register_handler('discord_linked')
def handle_discord_linked(profile, milestone, _cache=None):
    """Check if Discord is connected (Profile.is_discord_verified and Profile.discord_id)"""
    achieved = profile.is_discord_verified and profile.discord_id is not None
    progress = 1 if achieved else 0
    return {'achieved': achieved, 'progress': progress}

@register_handler('rating_count')
def handle_rating_count(profile, milestone, _cache=None):
    """Check progress for number of games rated (UserConceptRating count)"""
    from trophies.models import UserConceptRating

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'rating_count' in _cache:
        current = _cache['rating_count']
    else:
        current = UserConceptRating.objects.filter(profile=profile).count()
        if _cache is not None:
            _cache['rating_count'] = current
    achieved = current >= target
    return {'achieved': achieved, 'progress': current}

@register_handler('playtime_hours')
def handle_playtime_hours(profile, milestone, _cache=None):
    """Check progress for total accumulated playtime in hours"""
    from django.db.models import Sum

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'playtime_hours' in _cache:
        current_hours = _cache['playtime_hours']
    else:
        total_duration = ProfileGame.objects.filter(
            profile=profile,
            play_duration__isnull=False
        ).aggregate(total=Sum('play_duration'))['total']

        if total_duration:
            current_hours = int(total_duration.total_seconds() / 3600)
        else:
            current_hours = 0
        if _cache is not None:
            _cache['playtime_hours'] = current_hours

    achieved = current_hours >= target
    return {'achieved': achieved, 'progress': current_hours}

@register_handler('trophy_count')
def handle_trophy_count(profile, milestone, _cache=None):
    """Check progress for total trophies earned (Profile.total_trophies)"""
    target = milestone.criteria_details.get('target', 0)
    current = profile.total_trophies
    achieved = current >= target
    return {'achieved': achieved, 'progress': current}

@register_handler('comment_upvotes')
def handle_comment_upvotes(profile, milestone, _cache=None):
    """Check progress for total comment upvotes received across all user comments"""
    from django.db.models import Sum
    from trophies.models import Comment

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'comment_upvotes' in _cache:
        current = _cache['comment_upvotes']
    else:
        total_upvotes = Comment.objects.filter(
            profile=profile,
            is_deleted=False
        ).aggregate(total=Sum('upvote_count'))['total']
        current = total_upvotes if total_upvotes else 0
        if _cache is not None:
            _cache['comment_upvotes'] = current

    achieved = current >= target
    return {'achieved': achieved, 'progress': current}

@register_handler('checklist_upvotes')
def handle_checklist_upvotes(profile, milestone, _cache=None):
    """Check progress for total checklist upvotes received across all user checklists"""
    from django.db.models import Sum
    from trophies.models import Checklist

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'checklist_upvotes' in _cache:
        current = _cache['checklist_upvotes']
    else:
        total_upvotes = Checklist.objects.filter(
            profile=profile,
            is_deleted=False
        ).aggregate(total=Sum('upvote_count'))['total']
        current = total_upvotes if total_upvotes else 0
        if _cache is not None:
            _cache['checklist_upvotes'] = current

    achieved = current >= target
    return {'achieved': achieved, 'progress': current}


@register_handler('badge_count')
def handle_badge_count(profile, milestone, _cache=None):
    """Check progress for total badges earned (ProfileGamification.total_badges_earned)"""
    from trophies.models import ProfileGamification

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'badge_count' in _cache:
        current = _cache['badge_count']
    else:
        try:
            current = profile.gamification.total_badges_earned
        except ProfileGamification.DoesNotExist:
            current = 0
        if _cache is not None:
            _cache['badge_count'] = current
    achieved = current >= target
    return {'achieved': achieved, 'progress': current}


@register_handler('completion_count')
def handle_completion_count(profile, milestone, _cache=None):
    """Check progress for games at 100% completion (Profile.total_completes)"""
    target = milestone.criteria_details.get('target', 0)
    current = profile.total_completes
    achieved = current >= target
    return {'achieved': achieved, 'progress': current}


@register_handler('stage_count')
def handle_stage_count(profile, milestone, _cache=None):
    """Check progress for total badge stages completed across all badge series"""
    from django.db.models import Sum
    from trophies.models import UserBadgeProgress

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'stage_count' in _cache:
        total = _cache['stage_count']
    else:
        total = UserBadgeProgress.objects.filter(profile=profile).aggregate(
            total=Sum('completed_concepts')
        )['total'] or 0
        if _cache is not None:
            _cache['stage_count'] = total
    achieved = total >= target
    return {'achieved': achieved, 'progress': total}


@register_handler('az_progress')
def handle_az_progress(profile, milestone, _cache=None):
    """Check progress for A-Z challenge letters completed (across all challenges)"""
    from django.db.models import Sum
    from trophies.models import Challenge

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'az_progress' in _cache:
        total = _cache['az_progress']
    else:
        total = Challenge.objects.filter(
            profile=profile, challenge_type='az', is_deleted=False
        ).aggregate(total=Sum('completed_count'))['total'] or 0
        if _cache is not None:
            _cache['az_progress'] = total
    achieved = total >= target
    return {'achieved': achieved, 'progress': total}


@register_handler('genre_progress')
def handle_genre_progress(profile, milestone, _cache=None):
    """Check progress for Genre Challenge genres completed (across all challenges)."""
    from django.db.models import Sum
    from trophies.models import Challenge

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'genre_progress' in _cache:
        total = _cache['genre_progress']
    else:
        total = Challenge.objects.filter(
            profile=profile, challenge_type='genre', is_deleted=False
        ).aggregate(total=Sum('completed_count'))['total'] or 0
        if _cache is not None:
            _cache['genre_progress'] = total
    achieved = total >= target
    return {'achieved': achieved, 'progress': total}


@register_handler('subgenre_progress')
def handle_subgenre_progress(profile, milestone, _cache=None):
    """Check progress for unique subgenres collected (best single challenge)."""
    from trophies.models import Challenge

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'subgenre_progress' in _cache:
        best = _cache['subgenre_progress']
    else:
        best_challenge = Challenge.objects.filter(
            profile=profile, challenge_type='genre', is_deleted=False
        ).order_by('-subgenre_count').first()
        best = best_challenge.subgenre_count if best_challenge else 0
        if _cache is not None:
            _cache['subgenre_progress'] = best
    achieved = best >= target
    return {'achieved': achieved, 'progress': best}


# Calendar month handlers (parameterized for all 12 months)


def _handle_calendar_month(profile, milestone, _cache=None):
    """Check if a specific calendar month is fully filled with platinums."""
    from trophies.models import Challenge, CalendarChallengeDay, CALENDAR_DAYS_PER_MONTH
    from django.db.models import Count

    month_num = MONTH_MAP[milestone.criteria_type]
    days_needed = CALENDAR_DAYS_PER_MONTH[month_num]

    # Use consistent ordering: pick the most-progressed calendar challenge
    if _cache is not None and '_calendar_challenge' in _cache:
        challenge = _cache['_calendar_challenge']
    else:
        challenge = Challenge.objects.filter(
            profile=profile, challenge_type='calendar', is_deleted=False
        ).order_by('-completed_count').first()
        if _cache is not None:
            _cache['_calendar_challenge'] = challenge

    if not challenge:
        return {'achieved': False, 'progress': 0}

    # Pre-fetch all month counts in one grouped query (avoids 12 separate
    # COUNT queries when checking all calendar months in a batch)
    if _cache is not None and '_calendar_month_counts' in _cache:
        month_counts = _cache['_calendar_month_counts']
    else:
        rows = (
            CalendarChallengeDay.objects.filter(challenge=challenge, is_filled=True)
            .values('month')
            .annotate(filled=Count('id'))
        )
        month_counts = {r['month']: r['filled'] for r in rows}
        if _cache is not None:
            _cache['_calendar_month_counts'] = month_counts

    filled = month_counts.get(month_num, 0)
    achieved = filled >= days_needed
    return {'achieved': achieved, 'progress': filled}


for _month_key in MONTH_MAP:
    register_handler(_month_key)(_handle_calendar_month)


@register_handler('calendar_months_total')
def handle_calendar_months_total(profile, milestone, _cache=None):
    """Check progress for total fully completed calendar months"""
    from trophies.models import Challenge, CalendarChallengeDay, CALENDAR_DAYS_PER_MONTH

    target = milestone.criteria_details.get('target', 0)

    if _cache is not None and '_calendar_challenge' in _cache:
        challenge = _cache['_calendar_challenge']
    else:
        challenge = Challenge.objects.filter(
            profile=profile, challenge_type='calendar', is_deleted=False
        ).order_by('-completed_count').first()
        if _cache is not None:
            _cache['_calendar_challenge'] = challenge

    if not challenge:
        return {'achieved': False, 'progress': 0}

    from django.db.models import Count
    month_counts = dict(
        CalendarChallengeDay.objects.filter(challenge=challenge, is_filled=True)
        .values('month')
        .annotate(count=Count('id'))
        .values_list('month', 'count')
    )
    completed_months = sum(
        1 for month_num, days_needed in CALENDAR_DAYS_PER_MONTH.items()
        if month_counts.get(month_num, 0) >= days_needed
    )

    achieved = completed_months >= target
    return {'achieved': achieved, 'progress': completed_months}


@register_handler('calendar_complete')
def handle_calendar_complete(profile, milestone, _cache=None):
    """Check if the calendar challenge is fully complete (365/365)"""
    from trophies.models import Challenge

    complete = Challenge.objects.filter(
        profile=profile, challenge_type='calendar', is_complete=True, is_deleted=False
    ).exists()
    progress = 1 if complete else 0
    return {'achieved': complete, 'progress': progress}


@register_handler('is_premium')
def handle_is_premium(profile, milestone, _cache=None):
    """Check if the user is currently a premium subscriber"""
    achieved = profile.user_is_premium
    progress = 1 if achieved else 0
    return {'achieved': achieved, 'progress': progress}


@register_handler('subscription_months')
def handle_subscription_months(profile, milestone, _cache=None):
    """Check progress for accumulated subscription months (30-day periods)."""
    from django.utils import timezone

    target = milestone.criteria_details.get('target', 0)

    if _cache is not None and 'subscription_months' in _cache:
        current = _cache['subscription_months']
    else:
        current = 0
        if profile.user:
            from users.models import SubscriptionPeriod
            now = timezone.now()
            total_days = 0
            for started, ended in SubscriptionPeriod.objects.filter(
                user=profile.user
            ).values_list('started_at', 'ended_at'):
                end = ended or now
                delta = (end - started).days
                if delta > 0:
                    total_days += delta
            current = total_days // 30
        if _cache is not None:
            _cache['subscription_months'] = current

    achieved = current >= target
    return {'achieved': achieved, 'progress': current}
