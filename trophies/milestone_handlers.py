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
def handle_manual(profile, milestone):
    """Always return not achieved unless already awarded."""
    try:
        user_milestone = profile.user_milestones.get(milestone=milestone)
        return {'achieved': True, 'progress': milestone.required_value, 'updated': False}
    except ObjectDoesNotExist:
        return {'achieved': False, 'progress': 0, 'updated': False}

@register_handler('plat_count')
def handle_plat_count(profile, milestone):
    """Check progress for plat_count"""
    target = milestone.criteria_details.get('target', 0)
    filter_kwargs = {}
    if 'filter' in milestone.criteria_details:
        pass

    current = ProfileGame.objects.filter(profile=profile, has_plat=True, **filter_kwargs).count()
    achieved = current >= target
    return {'achieved': achieved, 'progress': current, 'updated': True}

@register_handler('psn_linked')
def handle_psn_linked(profile, milestone):
    """Check if PSN profile is linked to a user account (Profile.is_linked)"""
    achieved = profile.is_linked
    progress = 1 if achieved else 0
    return {'achieved': achieved, 'progress': progress, 'updated': True}

@register_handler('discord_linked')
def handle_discord_linked(profile, milestone):
    """Check if Discord is connected (Profile.is_discord_verified and Profile.discord_id)"""
    achieved = profile.is_discord_verified and profile.discord_id is not None
    progress = 1 if achieved else 0
    return {'achieved': achieved, 'progress': progress, 'updated': True}

@register_handler('rating_count')
def handle_rating_count(profile, milestone):
    """Check progress for number of games rated (UserConceptRating count)"""
    from trophies.models import UserConceptRating

    target = milestone.criteria_details.get('target', 0)
    current = UserConceptRating.objects.filter(profile=profile).count()
    achieved = current >= target
    return {'achieved': achieved, 'progress': current, 'updated': True}

@register_handler('playtime_hours')
def handle_playtime_hours(profile, milestone):
    """Check progress for total accumulated playtime in hours"""
    from django.db.models import Sum
    from datetime import timedelta

    target = milestone.criteria_details.get('target', 0)

    # Sum all play_duration values for the profile
    total_duration = ProfileGame.objects.filter(
        profile=profile,
        play_duration__isnull=False
    ).aggregate(total=Sum('play_duration'))['total']

    # Convert total duration to hours
    if total_duration:
        current_hours = int(total_duration.total_seconds() / 3600)
    else:
        current_hours = 0

    achieved = current_hours >= target
    return {'achieved': achieved, 'progress': current_hours, 'updated': True}
