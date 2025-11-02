from django.core.cache import cache
from trophies.models import EarnedTrophy

OLDEST_TS_KEY = 'latest_platinums_oldest_ts'
OLDEST_TS_TIMEOUT = 3600

def get_latest_platinums(limit=10):
    recent_platinums = EarnedTrophy.objects.filter(
        earned=True,
        trophy__trophy_type='platinum'
    ).select_related('profile', 'trophy', 'trophy__game').order_by('-earned_date_time')[:limit]

    recent_platinums_list = list(recent_platinums)

    enriched = []
    for et in recent_platinums_list:
        enriched.append({
            'image': et.trophy.trophy_icon_url,
            'profile': et.profile.psn_username,
            'trophy': et.trophy.trophy_name,
            'type': et.trophy.trophy_type.capitalize(),
            'game': et.trophy.game.title_name,
            'time': et.earned_date_time,
            'slug': f"/games/{et.trophy.game.np_communication_id}/",
        })
    
    if recent_platinums_list:
        oldest_ts = recent_platinums_list[-1].earned_date_time.isoformat()
        cache.set(OLDEST_TS_KEY, oldest_ts, OLDEST_TS_TIMEOUT * 2)

    return enriched