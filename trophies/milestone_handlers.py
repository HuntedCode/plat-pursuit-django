"""Criteria handlers for the LEGACY milestone engine.

Only the award types this engine still genuinely owns remain. The trophy-facing ladders
(plat/trophy/completion/badge counts, playtime, subscription tenure) were superseded by the
`milestones` app; checklist/review criteria died with Checklists and the Review Hub; the
challenge criteria died with the Challenge system. Their Milestone rows are retired via
`manage.py retire_milestones` (is_active=False, granted titles removed, earned records kept),
so `Milestone.objects.active()` never evaluates them.

Remaining here: manual awards (easter eggs, fundraiser patron), link/premium one-offs, rating
count, and the two legacy badge-stat counts. See docs/design/milestones-revamp.md.
"""
from django.core.exceptions import ObjectDoesNotExist

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
