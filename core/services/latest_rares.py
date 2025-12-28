from typing import List, Dict
from django.utils import timezone
from datetime import timedelta
from trophies.models import EarnedTrophy

def get_latest_psn_rares(limit: int = 5) -> List[Dict]:
    week_ago = timezone.now() - timedelta(days=7)
    rare_trophies = EarnedTrophy.objects.filter(
        earned=True,
        earned_date_time__gte=week_ago
    ).select_related('trophy', 'trophy__game', 'profile').order_by('trophy__trophy_earn_rate')[:limit]

    return _enrich_rares(rare_trophies, 'psn')

def get_latest_pp_rares(limit: int = 5) -> List[Dict]:
    week_ago = timezone.now() - timedelta(days=7)
    rare_trophies = EarnedTrophy.objects.filter(
        earned=True,
        earned_date_time__gte=week_ago
    ).select_related('trophy', 'trophy__game', 'profile').order_by('trophy__earn_rate')[:limit]

    return _enrich_rares(rare_trophies, 'pp')

def _enrich_rares(qs, rare_type: str):
    enriched = []
    for et in qs:
        rate = et.trophy.trophy_earn_rate if rare_type == 'psn' else et.trophy.earn_rate * 100
        rarity = _get_psn_rarity(et.trophy.trophy_rarity) if rare_type == 'psn' else et.trophy.get_pp_rarity_tier()
        enriched.append({
            'image': et.trophy.trophy_icon_url,
            'profile_name': et.profile.display_psn_username,
            'profile_flag': et.profile.flag or '',
            'profile_plats': et.profile.earned_trophy_summary.get('platinum', '') if et.profile.earned_trophy_summary else '',
            'profile_created_at': et.profile.created_at.strftime('%Y-%m-%d'),
            'trophy': et.trophy.trophy_name,
            'type': et.trophy.trophy_type,
            'game': et.trophy.game.title_name,
            'rate': rate,
            'rarity': rarity,
            'time': et.earned_date_time,
            'slug': f"/games/{et.trophy.game.np_communication_id}/#{et.trophy.trophy_id}"
        })
    return enriched

PSN_RARITY_MAP = {
    0: 'Ultra Rare',
    1: 'Very Rare',
    2: 'Rare',
}

def _get_psn_rarity(psn_value):
    return PSN_RARITY_MAP.get(psn_value, 'Common')