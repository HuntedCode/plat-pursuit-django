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
