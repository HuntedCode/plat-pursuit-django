from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from trophies.models import FeaturedProfile, Profile, EarnedTrophy

def get_featured_profile():
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    manual = FeaturedProfile.objects.filter(
        start_date__lte=now,
        end_date__gte=now
    ).select_related('profile').order_by('-priority').first()
    if manual:
        profile = manual.profile
    else:
        top_profile = Profile.objects.annotate(
            weekly_platinums=Count('earned_trophy_entries', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__earned_date_time__gte=week_ago, earned_trophy_entries__trophy__trophy_type='platinum')),
            weekly_trophies=Count('earned_trophy_entries', filter=Q(earned_trophy_entries__earned=True, earned_trophy_entries__earned_date_time__gte=week_ago))
        ).order_by('-weekly_platinums', '-weekly_trophies').first()
        profile = top_profile if top_profile else None
    
    if not profile:
        return {}
    
    return {
        'name': profile.psn_username,
        'avatar': profile.avatar_url,
        'platinums': {
            'total': EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum').count(),
            'weekly': EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum', earned_date_time__gte=week_ago).count()
        },
        'trophies': {
            'total': EarnedTrophy.objects.filter(profile=profile, earned=True).count(),
            'weekly': EarnedTrophy.objects.filter(profile=profile, earned=True, earned_date_time__gte=week_ago).count()
        },
        'bio': profile.about_me
    }