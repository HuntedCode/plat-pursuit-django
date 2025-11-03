from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from trophies.models import EarnedTrophy

def get_latest_psn_rares(limit=5):
    week_ago = timezone.now() - timedelta(days=7)
    rare_trophies = EarnedTrophy.objects.filter(
        earned=True,
        earned_date_time__gte=week_ago
    ).select_related('trophy', 'trophy__game', 'profile').order_by('trophy__trophy_earn_rate')[:limit]

    return _enrich_rares(rare_trophies, 'psn')

def get_latest_pp_rares(limit=5):
    week_ago = timezone.now() - timedelta(days=7)
    rare_trophies = EarnedTrophy.objects.filter(
        earned=True,
        earned_date_time__gte=week_ago
    ).select_related('trophy', 'trophy__game', 'profile').order_by('trophy__earn_rate')[:limit]

    return _enrich_rares(rare_trophies, 'pp')

def _enrich_rares(qs, type):
    enriched = []
    for et in qs:
        enriched.append({
            'image': et.trophy.trophy_icon_url,
            'profile': et.profile.psn_username,
            'trophy': et.trophy.trophy_name,
            'type': et.trophy.trophy_type,
            'game': et.trophy.game.title_name,
            'rate': et.trophy.trophy_earn_rate if type == 'psn' else et.trophy.earn_rate * 100,
            'rarity':_get_psn_rarity(et.trophy.trophy_rarity) if type == 'psn' else et.trophy.get_pp_rarity_tier(),
            'time': et.earned_date_time,
            'slug': f"/games/{et.trophy.game.np_communication_id}/"
        })
    return enriched

def _get_psn_rarity(psn_value):
    if psn_value == 0:
        return 'Ultra Rare'
    elif psn_value == 1:
        return 'Very Rare'
    elif psn_value == 2:
        return 'Rare'
    else:
        return 'Common'