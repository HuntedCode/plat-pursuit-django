from django.core.exceptions import ObjectDoesNotExist
from trophies.models import ProfileGame

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


@register_handler('unique_badge_count')
def handle_unique_badge_count(profile, milestone, _cache=None):
    """Check progress for unique badges earned (ProfileGamification.unique_badges_earned)"""
    from trophies.models import ProfileGamification

    target = milestone.criteria_details.get('target', 0)
    if _cache is not None and 'unique_badge_count' in _cache:
        current = _cache['unique_badge_count']
    else:
        try:
            current = profile.gamification.unique_badges_earned
        except ProfileGamification.DoesNotExist:
            current = 0
        if _cache is not None:
            _cache['unique_badge_count'] = current
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


# ── Review milestones ────────────────────────────────────────────────── #

@register_handler('review_count')
def handle_review_count(profile, milestone, _cache=None):
    """Count of non-deleted reviews with 150+ words."""
    from trophies.models import Review

    target = milestone.criteria_details.get('target', 0)
    cache_key = 'review_count'
    if _cache is not None and cache_key in _cache:
        current = _cache[cache_key]
    else:
        current = Review.objects.filter(
            profile=profile, is_deleted=False, word_count__gte=150
        ).count()
        if _cache is not None:
            _cache[cache_key] = current

    return {'achieved': current >= target, 'progress': current}


@register_handler('review_helpful_count')
def handle_review_helpful_count(profile, milestone, _cache=None):
    """Total helpful votes received across all non-deleted reviews."""
    from django.db.models import Sum
    from trophies.models import Review

    target = milestone.criteria_details.get('target', 0)
    cache_key = 'review_helpful_count'
    if _cache is not None and cache_key in _cache:
        current = _cache[cache_key]
    else:
        total = Review.objects.filter(
            profile=profile, is_deleted=False
        ).aggregate(total=Sum('helpful_count'))['total']
        current = total or 0
        if _cache is not None:
            _cache[cache_key] = current

    return {'achieved': current >= target, 'progress': current}
