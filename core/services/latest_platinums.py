from django.db.models import F
from django.utils import timezone
from datetime import timedelta
from trophies.models import EarnedTrophy

def get_latest_platinums(limit=10):
    month_ago = timezone.now() - timedelta(days=30)
    recent_platinums = EarnedTrophy.objects.filter(
        earned=True,
        trophy__trophy_type='platinum',
        earned_date_time__gte=month_ago
    ).select_related('profile', 'trophy', 'trophy__game').order_by(F('earned_date_time').desc(nulls_last=True))[:limit]

    enriched = []
    for et in recent_platinums:
        enriched.append({
            'image': et.trophy.trophy_icon_url,
            'profile_name': et.profile.display_psn_username,
            'profile_flag': et.profile.flag or '',
            'profile_plats': et.profile.earned_trophy_summary.get('platinum') if et.profile.earned_trophy_summary else '',
            'profile_created_at': et.profile.created_at.strftime('%Y-%m-%d'),
            'trophy': et.trophy.trophy_name,
            'type': et.trophy.trophy_type.capitalize(),
            'game': et.trophy.game.title_name,
            'time': et.earned_date_time,
            'slug': f"/games/{et.trophy.game.np_communication_id}/",
        })

    return enriched