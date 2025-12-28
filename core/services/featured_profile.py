from django.db.models import Count, Q, OuterRef, Exists
from django.urls import reverse_lazy
from django.utils import timezone
from datetime import timedelta
from trophies.models import FeaturedProfile, Profile, EarnedTrophy

def get_featured_profile():
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    manual = FeaturedProfile.objects.filter(Q(start_date__lte=now) & Q(end_date__gte=now)).select_related('profile').order_by('-priority').first()

    if not manual:
        return {}
    
    profile = manual.profile
    
    total_plats = profile.total_plats
    total_trophies = profile.total_trophies

    weekly_plat_filter = Q(trophy__trophy_type='platinum', earned=True, earned_date_time__gtw=week_ago)
    weekly_total_filter = Q(earned=True, earned_date_time__gte=week_ago)

    weekly_counts = EarnedTrophy.objects.filter(profile=profile).aggregate(
        weekly_plats=Count('id', filter=weekly_plat_filter),
        weekly_trophies=Count('id', filter=weekly_total_filter),
    )

    return {
        'name': profile.display_psn_username,
        'avatar': profile.avatar_url,
        'platinums': {
            'total': total_plats,
            'weekly': weekly_counts['weekly_plats'],
        },
        'trophies': {
            'total': total_trophies,
            'weekly': weekly_counts['weekly_trophies'],
        },
        'bio': profile.about_me,
        'slug': reverse_lazy('profile_detail', kwargs={'psn_username': profile.psn_username})
    }