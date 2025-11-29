from django.core.cache import cache
from django.db.models import F
from trophies.models import EarnedTrophy

def get_latest_platinums(limit=10):
    recent_platinums = EarnedTrophy.objects.filter(
        earned=True,
        trophy__trophy_type='platinum'
    ).select_related('profile', 'trophy', 'trophy__game').order_by(F('earned_date_time').desc(nulls_last=True))[:limit]

    recent_platinums_list = list(recent_platinums)

    enriched = []
    for et in recent_platinums_list:
        enriched.append({
            'image': et.trophy.trophy_icon_url,
            'profile_name': et.profile.display_psn_username,
            'profile_flag': et.profile.flag if et.profile.flag else '',
            'profile_plats': et.profile.earned_trophy_summary['platinum'] if et.profile.earned_trophy_summary else '',
            'profile_created_at': et.profile.created_at.strftime('%Y-%m-%d'),
            'trophy': et.trophy.trophy_name,
            'type': et.trophy.trophy_type.capitalize(),
            'game': et.trophy.game.title_name,
            'time': et.earned_date_time,
            'slug': f"/games/{et.trophy.game.np_communication_id}/",
        })

    return enriched